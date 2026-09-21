"""C8's local, independently persisted Runtime and Gateway/rail processes.

This is a fixed synthetic order profile, not a distributed execution protocol
for a real exchange. A and B have different databases, writers and signing keys.
The Gateway serializes redemption/non-use in B, and asks A to durably claim a
live allocation before accepting an order. An interrupted claim retains charge.
Only explicitly delivered, signed B evidence enters A's normal Reconciler.
"""
from contextlib import closing
import json
import multiprocessing
import os
from pathlib import Path
import socket
import socketserver
import tempfile
import time

from .authority import Authority, live_grant
from .crypto import Signer, digest
from .domain_models import DomainPolicy, OrderProposal, parse_proposal
from .domain_rails import OrderRail
from .models import SAACError, require
from .protocol import verify_artifact
from .receipts import make_receipt
from .reconciliation import Reconciler
from .resolver import resolve
from .risk_book import RiskBook, decode, encode
from .service import SAACService

EPOCH = 1800000000
UNIT = 100
BUDGET = 'notional_usd_cents'
AUDIENCE = 'OMS-17'
MAX_MESSAGE = 8 * 1024 * 1024


def rpc(endpoint, command, **data):
    """Private, fixed-command JSON transport; no pickle or shell execution."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(endpoint))
            sock.sendall((encode({'command': command, **data}) + '\n').encode())
            with sock.makefile('rb') as stream:
                response = stream.readline(MAX_MESSAGE + 1)
        require(bool(response) and len(response) <= MAX_MESSAGE, 'REMOTE_UNAVAILABLE', 'No bounded remote response was available.')
        result = json.loads(response)
    except (OSError, ValueError) as error:
        raise SAACError('REMOTE_UNAVAILABLE', 'Remote eligibility or evidence is unavailable; retain the allocation.') from error
    if not result['ok']:
        raise SAACError(result['code'], result['message'])
    return result['value']


def _public_snapshot(book):
    with closing(book.connect()) as db:
        return {
            'events': book.events(db),
            'runs': [decode(r[0]) for r in db.execute('SELECT data FROM runs ORDER BY rowid')],
            'reservations': [{**dict(r), 'kappa': decode(r['kappa'])} for r in db.execute('SELECT * FROM reservations ORDER BY rowid')],
            'allocations': {r['reservation_id']: decode(r['data']) for r in db.execute('SELECT * FROM allocations ORDER BY rowid')},
            'nonces': [dict(r) for r in db.execute('SELECT * FROM nonces ORDER BY rowid')],
            'receipts': [decode(r[0]) for r in db.execute('SELECT data FROM receipts ORDER BY rowid')],
            'executions': [decode(r[0]) for r in db.execute('SELECT data FROM executions ORDER BY rowid')],
            'orders': [decode(r[0]) for r in db.execute('SELECT data FROM orders ORDER BY rowid')],
            'packs': [decode(r[0]) for r in db.execute('SELECT data FROM packs ORDER BY rowid')],
        }


class RemoteRuntime(SAACService):
    """Authority-only composition: no in-process protected rail or rail key."""
    def __init__(self, directory, rail_public, unsafe=False):
        self.directory, self.profile = Path(directory), 'trading'
        self.book = RiskBook(self.directory / 'book-A.sqlite')
        self.issuer = Signer(self.directory / 'keys/runtime.key', 'riskbook-1')
        self.base_clock, self.offset = lambda: EPOCH, 0
        self.rail_public, self.unsafe = rail_public, unsafe
        self.audience = AUDIENCE
        with self.book.transaction() as db:
            fresh = not db.execute('SELECT 1 FROM state').fetchone()
            if fresh:
                self._seed(db)
            self.offset = self.book.state(db).get('clock_offset', 0)
        self.authority = Authority(self.book, self.issuer, self.now)
        self.reconciler = Reconciler(self.book, rail_public, self.now)
        if fresh:
            self.publish(DomainPolicy(version=2, limits={BUDGET: 10 * UNIT}, per_action={BUDGET: UNIT},
                         resources=['instrument:XYZ/book:CASH-1'], routes=['BROKER-A/session17'],
                         price_min_cents=UNIT, price_max_cents=UNIT, max_ttl_seconds=300))
            with self.book.transaction() as db:
                self.book.event(db, None, 'C8_RUNTIME_CONFIGURED', {'unsafe_timeout_control': unsafe,
                    'store': 'A', 'scope': 'synthetic CASH-1 buy notional'}, self.now())

    def claim(self, kappa, proposal, close=False):
        """Commit eligibility once; subsequent policy changes cannot reopen it.

        Claim is the authority-use decision at A. The exact effect and allocation
        are frozen in that durable claim; B's eventual commit is its acceptance
        point. Revocation after a claim blocks new claims, never erases this one.
        B holds its own transaction throughout the request, so non-use and
        redemption cannot both win. A crash after claim and before B commit is
        unresolved dispatch, conservatively held, not proof of non-use.
        """
        verify_artifact(kappa, self.issuer.public, 'riskbook-1', 'saac-kappa')
        p = parse_proposal(proposal)
        require(p.operation == 'order.submit', 'REMOTE_PROFILE', 'C8 supports only exact synthetic buy orders.')
        with self.book.transaction() as db:
            row = db.execute('SELECT * FROM reservations WHERE id=?', (kappa['reservation']['id'],)).fetchone()
            require(row is not None and decode(row['kappa']) == kappa, 'NO_RESERVATION', 'A committed matching allocation is required.')
            require(kappa['aud'] == AUDIENCE and p.socket_id == AUDIENCE, 'WRONG_AUDIENCE', 'Authority belongs to the fixed order Gateway.')
            require(all(getattr(p, key) == kappa[key] for key in ('principal_id', 'agent_id', 'grant_id')),
                    'IDENTITY_BINDING', 'The authenticated fixture identity must match the capability.')
            nonce = db.execute('SELECT * FROM nonces WHERE nonce=?', (kappa['nonce'],)).fetchone()
            execution_id = 'execution_c8_' + digest({'nonce': kappa['nonce'], 'aud': AUDIENCE}).split(':')[1][:32]
            require(nonce is not None, 'NO_RESERVATION', 'A durable nonce is required.')
            if close:
                require(nonce['status'] == 'unused', 'OUTCOME_UNCERTAIN', 'A claimed execution cannot be closed as unused; retain capacity.')
            else:
                require(nonce['status'] in ('unused', 'remote_claimed') and
                        (nonce['execution_id'] is None or nonce['execution_id'] == execution_id),
                        'REPLAY', 'Authority has already been closed or consumed.')
            require(row['status'] in ('reserved', 'uncertain', 'executing') and row['consumed'] == row['released'] == 0,
                    'RESERVATION_INACTIVE', 'A live unspent allocation is required.')
            allocation = self.book.allocation(db, row)
            require(set(allocation) == set(kappa['reservation']['bound']) and all(
                    value['bound'] == kappa['reservation']['bound'][key] and value['consumed'] == value['released'] == 0
                    for key, value in allocation.items()), 'RESERVATION_INACTIVE', 'Every allocation dimension must match.')
            if not close:
                require(kappa['iat'] <= self.now() < kappa['exp'], 'EXPIRED', 'Expired authority cannot start new remote execution.')
                state = self.book.state(db)
                verify_artifact(state['pack'], self.issuer.public, 'riskbook-1', 'saac-pack')
                require(digest(state['pack']) == state['pack_hash'] == kappa['pack_hash'], 'PACK_NOT_LIVE', 'The issuing policy is not live.')
                require(not state['breaker'], 'BREAKER_HALT', 'The institution has halted new execution.')
                grant = live_grant(self.book, db, p.grant_id, self.now())
                current = resolve(p, state, grant, db).model_dump()
                require(current['c'] == kappa['effect']['c'], 'STALE_STATE', 'The resolved resource or grant changed.')
                require(current == kappa['effect'] and digest(current) == kappa['effect_digest'], 'EFFECT_MISMATCH', 'The exact effect does not match.')
                run = self.book.run(db, row['run_id'])
                require(digest(run['snapshot']) == kappa['snapshot_hash'], 'SNAPSHOT_BINDING', 'The committed snapshot must match.')
            status = 'closed' if close else 'remote_claimed'
            db.execute('UPDATE nonces SET status=?,execution_id=? WHERE nonce=?', (status, execution_id, kappa['nonce']))
            db.execute("UPDATE reservations SET status='executing' WHERE id=?", (row['id'],))
            self.book.event(db, row['run_id'], 'REMOTE_NON_USE_CLAIMED' if close else 'REMOTE_REDEMPTION_CLAIMED',
                            {'kappa_id': kappa['kappa_id'], 'nonce': kappa['nonce'], 'execution_id': execution_id,
                             'effect_digest': kappa['effect_digest'], 'store': 'A'}, self.now())
            return {'execution_id': execution_id, 'effect_digest': kappa['effect_digest'], 'reservation_id': row['id']}

    def accept_remote_evidence(self, receipt):
        """Declared receipt inbox; importing evidence does not alter accounting.

        Signature/bindings are checked before retention. The unchanged normal
        Reconciler applies cumulative accounting after this explicit delivery.
        Evaluator snapshots never call this interface implicitly.
        """
        verify_artifact(receipt, self.rail_public, 'socket-1', 'saac-receipt')
        with self.book.transaction() as db:
            row = db.execute('SELECT * FROM reservations WHERE id=?', (receipt['reservation_id'],)).fetchone()
            require(row is not None, 'RECEIPT_BINDING', 'Receipt requires known authority.')
            kappa = decode(row['kappa'])
            require(all(receipt[key] == kappa[key] for key in ('nonce', 'kappa_id', 'snapshot_hash', 'aud')),
                    'RECEIPT_BINDING', 'Receipt does not bind the issued lifecycle.')
            nonce = db.execute('SELECT * FROM nonces WHERE nonce=?', (receipt['nonce'],)).fetchone()
            require(nonce['status'] != 'unused' and nonce['execution_id'] == receipt['execution_id'],
                    'RECEIPT_BINDING', 'Receipt must match the durable remote claim.')
            require(type(receipt['revision']) is int and receipt['revision'] > 0, 'RECEIPT_REVISION', 'Receipt needs a positive revision.')
            prior = db.execute('SELECT data FROM receipts WHERE reservation_id=? AND revision=?',
                               (row['id'], receipt['revision'])).fetchone()
            require(prior is None or decode(prior[0]) == receipt, 'RECEIPT_CONFLICT', 'Conflicting cumulative revision.')
            if prior is None:
                db.execute('INSERT INTO receipts VALUES (?,?,?,?)', (receipt['receipt_id'], row['id'], receipt['revision'], encode(receipt)))
                self.book.event(db, row['run_id'], 'REMOTE_EVIDENCE_DELIVERED', {'receipt': receipt, 'observer': 'store-B'}, self.now())
        try:
            return self.reconciler.accept(receipt)
        except SAACError as error:
            with self.book.transaction() as db:
                self.book.event(db, row['run_id'], 'REMOTE_EVIDENCE_REJECTED',
                                {'receipt': receipt, 'code': error.code, 'message': error.message}, self.now())
            raise

    def unsafe_timeout_release(self):
        require(self.unsafe, 'UNSAFE_CONTROL_DISABLED', 'Unsupported release belongs only to the isolated negative control.')
        with self.book.transaction() as db:
            for row in db.execute("SELECT * FROM reservations WHERE status NOT IN ('released','settled')").fetchall():
                values = self.book.allocation(db, row)
                before = self.book.risk(db)
                db.execute("UPDATE reservations SET released=bound-consumed,status='released' WHERE id=?", (row['id'],))
                self.book.settle_dimensions(db, row, {'consumed': {k: v['consumed'] for k, v in values.items()},
                    'released': {k: v['bound']-v['consumed'] for k, v in values.items()}})
                self.book.event(db, row['run_id'], 'UNSAFE_TIMEOUT_RELEASE', {'reservation_id': row['id'],
                    'unsupported_by_evidence': True, 'risk_before': before, 'risk_after': self.book.risk(db)}, self.now())
        return self.snapshot()

    def snapshot(self):
        result = _public_snapshot(self.book)
        with closing(self.book.connect()) as db:
            result.update(state=self.book.state(db), grants=[decode(r[0]) for r in db.execute('SELECT data FROM grants')],
                          now=self.now(), risk=self.book.risk(db), keys={'riskbook-1': self.issuer.public, 'socket-1': self.rail_public})
        result['head'] = result['events'][-1]['hash'] if result['events'] else 'genesis'
        result['process'] = {'pid': os.getpid(), 'role': 'runtime', 'store': 'A'}
        result['attestation'] = self.issuer.sign({'typ': 'coverage-book-checkpoint', 'head': result['head'],
            'snapshot_digest': digest(result), 'now': self.now(),
            'evidence_class': 'local_fixture_attestation_not_accepted_outcome_authority'})
        return result

    def dispatch(self, request):
        command = request['command']
        if command == 'health': return {'pid': os.getpid(), 'store': 'A', 'public': self.issuer.public}
        if command == 'snapshot': return self.snapshot()
        if command == 'authorize': return self.authority.propose(parse_proposal(request['proposal']), request_id=request['request_id'])
        if command == 'claim': return self.claim(request['kappa'], request['proposal'], request.get('close', False))
        if command == 'evidence': return self.accept_remote_evidence(request['receipt'])
        if command == 'unsafe_timeout_release': return self.unsafe_timeout_release()
        if command == 'expire_authority': return self.advance(301)
        if command == 'halt':
            self.breaker(True)
            return self.snapshot()
        require(False, 'REMOTE_COMMAND', 'Unknown fixed Runtime command.')


class IndependentRail:
    def __init__(self, directory, runtime_endpoint, issuer_public):
        self.book = RiskBook(Path(directory) / 'journal-B.sqlite')
        self.signer = Signer(Path(directory) / 'keys/rail.key', 'socket-1')
        self.runtime_endpoint, self.issuer_public = runtime_endpoint, issuer_public

    def redeem(self, kappa, proposal, close=False, interrupt_after_claim=False):
        verify_artifact(kappa, self.issuer_public, 'riskbook-1', 'saac-kappa')
        p = parse_proposal(proposal)
        require(kappa['aud'] == AUDIENCE and p.socket_id == AUDIENCE, 'WRONG_AUDIENCE', 'Wrong protected rail.')
        require(p.operation == 'order.submit', 'REMOTE_PROFILE', 'Only fixed order submissions are supported.')
        require(all(getattr(p, key) == kappa[key] for key in ('principal_id', 'agent_id', 'grant_id')),
                'IDENTITY_BINDING', 'Identity must match the signed capability.')
        # The signed exact effect must also match retries, even when A is down.
        x = kappa['effect']['x']
        require(all(getattr(p, key) == x[key] for key in ('quantity', 'limit_price_cents', 'route', 'side', 'time_in_force'))
                and kappa['effect']['r'] == f'instrument:{p.instrument}/book:{p.book}',
                'EFFECT_MISMATCH', 'Retry changed the signed effect.')
        require(digest(kappa['effect']) == kappa['effect_digest'], 'EFFECT_MISMATCH', 'Signed effect digest mismatch.')
        with self.book.transaction() as db:
            prior = db.execute('SELECT data FROM executions WHERE reservation_id=?', (kappa['reservation']['id'],)).fetchone()
            if prior:
                execution = decode(prior[0])
                receipt = decode(db.execute('SELECT data FROM receipts WHERE reservation_id=? ORDER BY revision DESC LIMIT 1',
                                            (kappa['reservation']['id'],)).fetchone()[0])
                require(execution['result']['status'] != 'non_use' or close, 'REPLAY', 'This capability was atomically closed unused.')
                return {'accepted': execution['result']['status'] != 'non_use', 'replay': True, 'execution': execution, 'receipt': receipt}
            claim = rpc(self.runtime_endpoint, 'claim', kappa=kappa, proposal=p.model_dump(), close=close)
            require(claim['effect_digest'] == kappa['effect_digest'] and claim['reservation_id'] == kappa['reservation']['id'],
                    'REMOTE_BINDING', 'The remote eligibility claim did not bind this lifecycle.')
            if interrupt_after_claim:
                raise SAACError('INTERRUPTED_DISPATCH', 'Fixed fault after durable A claim and before B commit; allocation retained.')
            execution_id = claim['execution_id']
            result = {'status': 'non_use', 'consumed': {BUDGET: 0}, 'released': kappa['reservation']['bound']} if close else OrderRail().execute(db, kappa['effect'], execution_id, 'full')
            execution = {'id': execution_id, 'revision': 1, 'result': result}
            receipt = make_receipt(self.signer, kappa, execution, EPOCH)
            db.execute('INSERT INTO nonces VALUES (?,?,?)', (kappa['nonce'], 'closed' if close else 'consumed', execution_id))
            db.execute('INSERT INTO executions VALUES (?,?,?)', (execution_id, kappa['reservation']['id'], encode(execution)))
            db.execute('INSERT INTO receipts VALUES (?,?,?,?)', (receipt['receipt_id'], kappa['reservation']['id'], 1, encode(receipt)))
            self.book.event(db, None, 'REMOTE_NON_USE_CLOSED' if close else 'REMOTE_EXECUTION_ACCEPTED',
                {'kappa': kappa, 'claim': claim, 'execution': execution, 'receipt': receipt}, EPOCH)
            return {'accepted': not close, 'replay': False, 'execution': execution, 'receipt': receipt}

    def fill(self, reservation_id):
        with self.book.transaction() as db:
            previous = db.execute('SELECT data FROM executions WHERE reservation_id=?', (reservation_id,)).fetchone()
            require(previous is not None, 'UNKNOWN_ORDER', 'Accepted order required.')
            execution = decode(previous[0])
            order = execution['result'].get('order')
            require(order is not None and order['status'] == 'working', 'ORDER_TERMINAL', 'A working order is required.')
            receipt = decode(db.execute('SELECT data FROM receipts WHERE reservation_id=? ORDER BY revision DESC LIMIT 1', (reservation_id,)).fetchone()[0])
            # κ comes from B's own immutable acceptance event, never from A.
            event = next(e for e in self.book.events(db) if e['kind'] == 'REMOTE_EXECUTION_ACCEPTED' and e['data']['kappa']['reservation']['id'] == reservation_id)
            kappa = event['data']['kappa']
            order.update(filled=order['quantity'], notional=order['quantity']*order['limit_price_cents'], status='filled', version=order['version']+1)
            execution['revision'] += 1
            execution['result'] = {'status': 'filled', 'order': order, 'consumed': {BUDGET: order['notional']}, 'released': {BUDGET: 0}}
            receipt = make_receipt(self.signer, kappa, execution, EPOCH)
            db.execute('UPDATE executions SET data=? WHERE id=?', (encode(execution), execution['id']))
            db.execute('UPDATE orders SET data=? WHERE id=?', (encode(order), order['id']))
            db.execute('INSERT INTO receipts VALUES (?,?,?,?)', (receipt['receipt_id'], reservation_id, receipt['revision'], encode(receipt)))
            self.book.event(db, None, 'REMOTE_FILL_ACCEPTED', {'kappa': kappa, 'execution': execution, 'receipt': receipt}, EPOCH)
            return receipt

    def snapshot(self):
        result = _public_snapshot(self.book)
        result.update(keys={'riskbook-1': self.issuer_public, 'socket-1': self.signer.public},
                      head=result['events'][-1]['hash'] if result['events'] else 'genesis',
                      process={'pid': os.getpid(), 'role': 'gateway_rail', 'store': 'B'})
        result['attestation'] = self.signer.sign({'typ': 'coverage-book-checkpoint', 'head': result['head'],
            'snapshot_digest': digest(result), 'now': EPOCH,
            'evidence_class': 'local_fixture_attestation_not_accepted_outcome_authority'})
        return result

    def dispatch(self, request):
        command = request['command']
        if command == 'health': return {'pid': os.getpid(), 'store': 'B', 'public': self.signer.public}
        if command == 'snapshot': return self.snapshot()
        if command in ('redeem', 'close_unused'):
            return self.redeem(request['kappa'], request['proposal'], command == 'close_unused', request.get('interrupt_after_claim', False))
        if command == 'fill': return self.fill(request['reservation_id'])
        require(False, 'REMOTE_COMMAND', 'Unknown fixed Gateway command.')


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            require(len(raw) <= MAX_MESSAGE, 'REMOTE_MESSAGE_SIZE', 'Request exceeds the fixed transport limit.')
            value = self.server.service.dispatch(json.loads(raw))
            response = {'ok': True, 'value': value}
        except SAACError as error:
            response = {'ok': False, 'code': error.code, 'message': error.message}
        except (KeyError, ValueError, TypeError):
            response = {'ok': False, 'code': 'REMOTE_MALFORMED', 'message': 'Malformed fixed-command request.'}
        self.wfile.write((encode(response) + '\n').encode())


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def _serve(kind, directory, endpoint, options):
    Path(endpoint).unlink(missing_ok=True)
    service = RemoteRuntime(directory, **options) if kind == 'runtime' else IndependentRail(directory, **options)
    with _Server(endpoint, _Handler) as server:
        os.chmod(endpoint, 0o600)
        server.service = service
        server.serve_forever(poll_interval=0.05)


class RemoteRailLab:
    """Trusted local test controller. No worker object crosses a process boundary."""
    def __init__(self, directory, unsafe=False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.socket_directory = tempfile.TemporaryDirectory(prefix='risc-c8-')
        self.runtime_endpoint = str(Path(self.socket_directory.name) / 'a.sock')
        self.rail_endpoint = str(Path(self.socket_directory.name) / 'b.sock')
        self.context = multiprocessing.get_context('spawn')
        self.unsafe, self.process_history = unsafe, []
        self.runtime_process = self.rail_process = None
        # Each child creates and owns its own key; public-key discovery uses a
        # bootstrap rail instance with no issuer authority, before A can issue.
        self._start('rail', {'runtime_endpoint': self.runtime_endpoint, 'issuer_public': ''})
        rail_public = rpc(self.rail_endpoint, 'health')['public']
        self.rail_process.terminate()
        self.rail_process.join(5)
        self.process_history.append({'event': 'bootstrap_stop', 'role': 'rail', 'pid': self.rail_process.pid,
                                     'exit_code': self.rail_process.exitcode, 'store': 'B'})
        self._start('runtime', {'rail_public': rail_public, 'unsafe': unsafe})
        self.issuer_public = rpc(self.runtime_endpoint, 'health')['public']
        self.rail_public = rail_public
        self._start('rail', {'runtime_endpoint': self.runtime_endpoint, 'issuer_public': self.issuer_public})

    def _start(self, kind, options):
        endpoint = self.runtime_endpoint if kind == 'runtime' else self.rail_endpoint
        Path(endpoint).unlink(missing_ok=True)
        process = self.context.Process(target=_serve, args=(kind, str(self.directory / ('A' if kind == 'runtime' else 'B')), endpoint, options))
        process.start()
        setattr(self, f'{kind}_process', process)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not process.is_alive():
                raise RuntimeError(f'{kind} process failed to start: {process.exitcode}')
            if Path(endpoint).exists():
                try:
                    health = rpc(endpoint, 'health')
                    self.process_history.append({'event': 'start', 'role': kind, 'pid': health['pid'], 'store': health['store']})
                    return
                except SAACError:
                    pass
            time.sleep(0.01)
        self.close()
        raise RuntimeError(f'{kind} process startup timed out')

    def runtime(self, command, **data): return rpc(self.runtime_endpoint, command, **data)
    def rail(self, command, **data): return rpc(self.rail_endpoint, command, **data)

    def kill_runtime(self):
        pid = self.runtime_process.pid
        self.runtime_process.kill()
        self.runtime_process.join(5)
        require(not self.runtime_process.is_alive(), 'PROCESS_RESTART', 'Runtime termination failed.')
        self.process_history.append({'event': 'SIGKILL', 'role': 'runtime', 'pid': pid, 'exit_code': self.runtime_process.exitcode, 'store': 'A'})

    def restart_runtime(self):
        if self.runtime_process.is_alive(): self.kill_runtime()
        self._start('runtime', {'rail_public': self.rail_public, 'unsafe': self.unsafe})

    def close(self):
        for process in (self.runtime_process, self.rail_process):
            if process is not None and process.is_alive():
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
        self.socket_directory.cleanup()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def proposal():
    return OrderProposal(quantity=1, limit_price_cents=UNIT, ttl_seconds=300).model_dump()
