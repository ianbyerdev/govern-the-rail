"""C10: two local Runtime books governing one typed downstream allocation.

CoveringAllocation is part of the institutional Runtime responsibility, not an
actor adapter or an extra service role. Its commit precedes local issuance. The
shared prime journal is a synthetic consequential sink, called only by the
baseline protected Gateway after its normal exact-effect checks.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .authority import request_intent
from .coverage_common import BUDGET, EPOCH, SCALE, UNITS, export_book, observe_book, ordinary_tape, order_service
from .crypto import Signer, digest, verify
from .domain_models import OrderProposal, parse_proposal
from .domain_rails import OrderRail
from .models import require
from .protocol import verify_artifact
from .risk_book import decode, encode, uid

ACCOUNT = {'id': 'prime:CASH-1', 'type': 'synthetic_buy_notional_account',
           'resource': 'instrument:XYZ/book:CASH-1', 'audience': 'OMS-17',
           'route': 'BROKER-A/session17', 'unit': BUDGET, 'L_star': 10*SCALE}


class CoveringAllocation:
    """Durable, fail-closed covering allocation for the two institutional books.

    A held row consumes rights even when local issuance fails or its response is
    unknown. Only an explicit durable local denial releases an unissued hold.
    After issuance this experiment never releases a covering row; accepted work
    remains outstanding. The protocol intentionally strands ambiguous capacity
    until the same request identity recovers its local result.
    """
    def __init__(self, directory, split=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/'cover.sqlite'
        self.signer = Signer(directory/'keys/cover.key', 'cover-1')
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS holds (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            existing = db.execute('SELECT data FROM config WHERE id=1').fetchone()
            if existing:
                self.config = decode(existing[0])
                require(self.config['split'] == split, 'COVER_CONFIG', 'Conserved rights cannot be changed on reopen.')
            else:
                require(split is None or (set(split) == {'A', 'B'} and all(type(v) is int and v >= 0 for v in split.values())
                        and sum(split.values()) <= ACCOUNT['L_star']), 'SPLIT_CONSERVATION',
                        'Assigned rights must be nonnegative and conserved under the true ceiling.')
                self.config = self.signer.sign({'typ': 'coverage-cover-config', 'account': ACCOUNT,
                    'split': split, 'mode': 'split' if split is not None else 'covering'})
                db.execute('INSERT INTO config VALUES (1,?)', (encode(self.config),))
                self.event(db, 'COVER_CONFIGURED', self.config)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=30000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, kind, payload):
        last = db.execute('SELECT seq,data FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        body = {'typ': 'coverage-cover-event', 'seq': last['seq']+1 if last else 1,
                'prev_hash': digest(decode(last['data'])) if last else 'genesis',
                'kind': kind, 'data': payload, 'now': EPOCH}
        signed = self.signer.sign(body)
        db.execute('INSERT INTO events VALUES (?,?)', (signed['seq'], encode(signed)))

    def acquire(self, book_id, request_id, intent, bound):
        require(book_id in ('A', 'B') and type(bound) is int and bound > 0, 'COVER_REQUEST',
                'A known local book and positive integer bound are required.')
        key = digest({'book_id': book_id, 'request_id': request_id})
        with self.transaction() as db:
            prior = db.execute('SELECT data FROM holds WHERE id=?', (key,)).fetchone()
            if prior:
                hold = decode(prior[0])
                require(hold['intent'] == intent and hold['bound'] == bound, 'IDEMPOTENCY_CONFLICT',
                        'A held covering allocation cannot be retargeted on retry.')
                self.event(db, 'COVER_RETRIED', {'hold_id': key, 'status': hold['status']})
                return hold
            held = [decode(r[0]) for r in db.execute('SELECT data FROM holds')]
            occupancy = sum(v['bound'] for v in held if v['status'] in ('held', 'issued'))
            local = sum(v['bound'] for v in held if v['status'] in ('held', 'issued') and v['book_id'] == book_id)
            right = self.config['split'][book_id] if self.config['split'] is not None else ACCOUNT['L_star']
            allowed = occupancy+bound <= ACCOUNT['L_star'] and local+bound <= right
            hold = {'id': key, 'book_id': book_id, 'request_id': request_id, 'intent': intent,
                    'bound': bound, 'status': 'held' if allowed else 'denied',
                    'kappa_id': None, 'local_run_id': None, 'account_id': ACCOUNT['id'],
                    'available_before': ACCOUNT['L_star']-occupancy, 'assigned_right': right}
            db.execute('INSERT INTO holds VALUES (?,?)', (key, encode(hold)))
            self.event(db, 'COVER_HELD' if allowed else 'COVER_DENIED', hold)
            return hold

    def local_result(self, hold_id, run):
        with self.transaction() as db:
            hold = decode(db.execute('SELECT data FROM holds WHERE id=?', (hold_id,)).fetchone()[0])
            require(hold['request_id'] == run['request_id'], 'COVER_BINDING', 'Cover must bind the local request.')
            if hold['status'] == 'released':
                require('kappa' not in run and run['status'] == 'denied' and run['id'] == hold['local_run_id'],
                        'COVER_RELEASE', 'A closed unissued hold may only replay its original denial.')
                return hold
            if 'kappa' in run:
                require(hold['status'] in ('held', 'issued') and
                        run['kappa']['reservation']['bound'][BUDGET] == hold['bound'],
                        'COVER_BINDING', 'Usable local authority must have a covering allocation first.')
                require(hold['kappa_id'] in (None, run['kappa']['kappa_id']), 'COVER_BINDING',
                        'A hold cannot cover two distinct capabilities.')
                hold.update(status='issued', kappa_id=run['kappa']['kappa_id'], local_run_id=run['id'])
                kind = 'LOCAL_ISSUANCE_LINKED'
            else:
                require(run['status'] == 'denied' and hold['kappa_id'] is None,
                        'COVER_RELEASE', 'Only a definite unissued local denial closes this hold.')
                hold.update(status='released', local_run_id=run['id'])
                kind = 'UNISSUED_COVER_RELEASED'
            db.execute('UPDATE holds SET data=? WHERE id=?', (encode(hold), hold_id))
            self.event(db, kind, {'hold': hold, 'local_result': run})
            return hold

    def eligibility(self, book_id, run, capability):
        """Read eligibility; an unknown held result stays charged, never refunded."""
        key = digest({'book_id': book_id, 'request_id': run['request_id']})
        with self.transaction() as db:
            row = db.execute('SELECT data FROM holds WHERE id=?', (key,)).fetchone()
            require(row is not None, 'COVER_MISSING', 'A signed local capability alone is not shared allocation.')
            hold = decode(row[0])
            require(hold['status'] in ('held', 'issued') and hold['bound'] == capability['reservation']['bound'][BUDGET]
                    and hold['intent'] == digest(request_intent(parse_proposal(run['proposal'])))
                    and hold['kappa_id'] in (None, capability['kappa_id']), 'COVER_BINDING',
                    'A live covering allocation must bind this local lifecycle.')
            return hold

    def export(self):
        with self.transaction() as db:
            holds = [decode(r[0]) for r in db.execute('SELECT data FROM holds ORDER BY rowid')]
            events = [decode(r[0]) for r in db.execute('SELECT data FROM events ORDER BY seq')]
        result = {'config': self.config, 'holds': holds, 'events': events,
                'keys': {'cover-1': self.signer.public},
                'Q': sum(h['bound'] for h in holds if h['status'] in ('held', 'issued')),
                'head': digest(events[-1]) if events else 'genesis'}
        result['attestation'] = self.signer.sign({'typ': 'coverage-cover-checkpoint',
            'head': result['head'], 'snapshot_digest': digest(result), 'now': EPOCH,
            'evidence_class': 'local_fixture_attestation_not_accepted_outcome_authority'})
        return result


class CoveredAuthority:
    """Trusted Runtime composition: acquire shared rights before local issuance."""
    def __init__(self, inner, cover, book_id):
        self.inner, self.cover, self.book_id = inner, cover, book_id

    def propose(self, proposal, *, request_id, fault=None):
        proposal = parse_proposal(proposal)
        require(proposal.operation == 'order.submit' and isinstance(request_id, str) and request_id,
                'COVER_REQUEST', 'The coverage fixture mediates typed orders with durable request identities.')
        hold = self.cover.acquire(self.book_id, request_id, digest(request_intent(proposal)),
                                  proposal.quantity*proposal.limit_price_cents)
        if hold['status'] == 'denied':
            return {'id': hold['id'], 'request_id': request_id, 'status': 'denied',
                    'proposal': proposal.model_dump(), 'decision': {'verdict': 'deny',
                    'code': 'SHARED_HEADROOM', 'message': 'The true downstream allocation has insufficient headroom.'},
                    'covering_hold': hold}
        if fault == 'before_local':
            raise RuntimeError('Injected local issuance failure; covering allocation remains held.')
        run = self.inner.propose(proposal, request_id=request_id)
        if fault == 'after_local':
            raise RuntimeError('Injected ambiguous local response; allocation and original capability remain durable.')
        result = self.cover.local_result(hold['id'], run)
        return {**run, 'covering_hold': result}


class PrimeJournal:
    """One typed synthetic downstream sink, with signed durable acceptances."""
    def __init__(self, directory, mode, issuer_keys, cover=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/'prime.sqlite'
        self.signer = Signer(directory/'keys/prime.key', 'prime-1')
        self.mode, self.issuer_keys, self.cover = mode, issuer_keys, cover
        self.config = self.signer.sign({'typ': 'coverage-prime-config', 'account': ACCOUNT,
                                       'mode': mode, 'issuer_keys': issuer_keys})
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS accepted (lifecycle TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=30000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def accept(self, book_id, run, capability, effect, order):
        verify_artifact(capability, self.issuer_keys[book_id], 'riskbook-1', 'saac-kappa')
        require(capability['effect'] == effect and capability['effect_digest'] == digest(effect)
                and effect['s'] == ACCOUNT['audience'] and effect['r'] == ACCOUNT['resource']
                and effect['x']['route'] == ACCOUNT['route'], 'PRIME_BINDING',
                'Downstream acceptance is bound to this typed account and exact effect.')
        require(effect['o'] == 'order.submit' and order['quantity']*order['limit_price_cents'] ==
                capability['reservation']['bound'][BUDGET], 'PRIME_BOUND', 'Order and authority must have one bound.')
        hold = self.cover.eligibility(book_id, run, capability) if self.cover is not None else None
        require(self.mode == 'unsafe' or hold is not None, 'COVER_MISSING', 'Safe downstream mode needs covering rights.')
        lifecycle = f"{book_id}:{capability['kappa_id']}"
        with self.transaction() as db:
            prior = db.execute('SELECT data FROM accepted WHERE lifecycle=?', (lifecycle,)).fetchone()
            if prior:
                recorded = decode(prior[0])
                require(recorded['capability'] == capability, 'PRIME_REPLAY', 'A lifecycle cannot be retargeted.')
                return recorded
            previous = [decode(r[0]) for r in db.execute('SELECT data FROM accepted ORDER BY rowid')]
            bound = capability['reservation']['bound'][BUDGET]
            if self.mode != 'unsafe':
                require(sum(v['bound'] for v in previous)+bound <= ACCOUNT['L_star'],
                        'SHARED_HEADROOM', 'Protected prime cannot accept beyond the covering account ceiling.')
            record = self.signer.sign({'typ': 'coverage-prime-acceptance', 'seq': len(previous)+1,
                'prev_hash': digest(previous[-1]) if previous else 'genesis',
                'book_id': book_id, 'lifecycle': lifecycle, 'account_id': ACCOUNT['id'],
                'capability': capability, 'effect': effect, 'order': order, 'bound': bound,
                'covering_hold_id': hold['id'] if hold else None, 'now': EPOCH})
            db.execute('INSERT INTO accepted VALUES (?,?)', (lifecycle, encode(record)))
            return record

    def export(self):
        with self.transaction() as db:
            records = [decode(r[0]) for r in db.execute('SELECT data FROM accepted ORDER BY rowid')]
        result = {'config': self.config, 'records': records, 'keys': {'prime-1': self.signer.public},
                'E': sum(r['order']['notional'] + (r['order']['quantity']-r['order']['filled'])*
                         r['order']['limit_price_cents'] for r in records),
                'head': digest(records[-1]) if records else 'genesis'}
        result['attestation'] = self.signer.sign({'typ': 'coverage-prime-checkpoint',
            'head': result['head'], 'snapshot_digest': digest(result), 'now': EPOCH,
            'evidence_class': 'local_fixture_attestation_not_accepted_outcome_authority'})
        return result


class PrimeOrderRail:
    """Carries baseline Gateway authority to the sink; owns no keys or budget."""
    def __init__(self, book, book_id, prime):
        self.book, self.book_id, self.prime = book, book_id, prime

    def execute(self, db, effect, execution_id, mode):
        row = db.execute('SELECT r.* FROM reservations r JOIN nonces n ON n.nonce=r.nonce '
                         'WHERE n.execution_id=?', (execution_id,)).fetchone()
        require(row is not None, 'PRIME_AUTHORITY', 'Only protected Gateway redemption may reach this adapter.')
        capability = decode(row['kappa'])
        result = OrderRail().execute(db, effect, execution_id, mode)
        record = self.prime.accept(self.book_id, self.book.run(db, row['run_id']), capability, effect, result['order'])
        # A previously accepted downstream lifecycle survives a local rollback.
        # Reconstruct the same accepted order, retaining allocation throughout.
        if record['order']['id'] != result['order']['id']:
            db.execute('DELETE FROM orders WHERE id=?', (result['order']['id'],))
            restored = {**record['order'], 'execution_id': execution_id}
            db.execute('INSERT OR REPLACE INTO orders VALUES (?,?)', (restored['id'], encode(restored)))
            result['order'] = restored
        # The prime's durable lifecycle can predate a rolled-back local attempt.
        # Keep that downstream identity separate from the new local execution
        # ID needed by the baseline receipt/order tables, and sign the mapping
        # inside the ordinary Gateway receipt. A retry cannot mint another
        # downstream acceptance or silently replace its historical identity.
        result['prime_acceptance_hash'] = digest(record)
        result['prime_execution_id'] = record['order']['execution_id']
        return result


def configuration(directory, mode):
    directory = Path(directory)
    split = {'A': 6*SCALE, 'B': 4*SCALE} if mode == 'split' else None
    services = {name: order_service(directory/name, split[name] if split else 10*SCALE) for name in ('A', 'B')}
    cover = None if mode == 'unsafe' else CoveringAllocation(directory/'cover', split)
    prime = PrimeJournal(directory/'prime', mode, {name: s.issuer.public for name, s in services.items()}, cover)
    for name, service in services.items():
        if cover is not None:
            service.authority = CoveredAuthority(service.authority, cover, name)
        service.socket.rails['order.submit'] = PrimeOrderRail(service.book, name, prime)
    return services, cover, prime


def capture(services, cover, prime, checkpoint_id):
    books = {name: export_book(service) for name, service in services.items()}
    observers = {name: observe_book(book, services[name].now()) for name, book in books.items()}
    rail = prime.export()
    # Once accepted, the prime's signed durable record replaces the promise.
    records = {r['lifecycle']: r for r in rail['records']}
    lifecycles = []
    for name, observer in observers.items():
        for life in observer['lifecycles']:
            lifecycle = f"{name}:{life['kappa_id']}"
            accepted = records.get(lifecycle)
            if accepted:
                order = accepted['order']
                consumed = order['notional']
                residual = (order['quantity']-order['filled'])*order['limit_price_cents']
                life = {**life, 'E': consumed+residual, 'consumed': consumed, 'residual': residual,
                        'evidence_domain': 'accepted_prime_execution'}
            lifecycles.append({**life, 'book_id': name, 'lifecycle': lifecycle})
    promises = [v for v in lifecycles if v['evidence_domain'] == 'usable_unredeemed_promise']
    local = {name: {'U': b['risk']['budgets'][BUDGET]['used'], 'Q': b['risk']['budgets'][BUDGET]['reserved'],
                    'L': b['risk']['budgets'][BUDGET]['limit'], 'E': observers[name]['E'],
                    'available': b['risk']['budgets'][BUDGET]['available'],
                    'ledger_compliant': b['risk']['budgets'][BUDGET]['available'] >= 0,
                    'coverage': observers[name]['E'] <= b['risk']['budgets'][BUDGET]['used'] + b['risk']['budgets'][BUDGET]['reserved']}
             for name, b in books.items()}
    used, reserved = sum(v['U'] for v in local.values()), sum(v['Q'] for v in local.values())
    obligation = sum(v['E'] for v in lifecycles)
    cover_export = cover.export() if cover else None
    oracle = {'E': obligation, 'lifecycles': lifecycles, 'outstanding_promises': len(promises),
              'promise_obligation': sum(v['E'] for v in promises), 'oracle_is_accepted_evidence': False,
              'evidence_domain': 'issuance_joined_with_prime' if promises else 'prime_execution_journal'}
    return {'id': checkpoint_id, 'now': EPOCH, 'U': used, 'Q': reserved, 'L': ACCOUNT['L_star'],
            'L_star': ACCOUNT['L_star'], 'E': obligation, 'available': ACCOUNT['L_star']-used-reserved,
            'ledger_compliant': used+reserved <= ACCOUNT['L_star'], 'coverage': obligation <= used+reserved,
            'local_ledger_compliant': all(v['ledger_compliant'] for v in local.values()),
            'shared_ceiling_compliant': obligation <= ACCOUNT['L_star'],
            'coverage_gap': obligation-used-reserved, 'outstanding_promises': len(promises),
            'evidence_domain': oracle['evidence_domain'], 'oracle': oracle, 'local_books': local,
            'covering': cover_export, 'snapshot': {'books': books, 'rail': rail, 'covering': cover_export}}


def run_c10(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    branches = {}
    for mode in ('unsafe', 'covering', 'split'):
        services, cover, prime = configuration(directory/mode, mode)
        admissions = {}
        for name, quantity in (('A', 6 if mode == 'split' else 10), ('B', 4 if mode == 'split' else 10)):
            admissions[name] = services[name].authority.propose(OrderProposal(quantity=quantity, limit_price_cents=SCALE),
                                                               request_id=f'c10:{name}:original')
        checkpoints = [capture(services, cover, prime, 'promises')]
        for name, run in admissions.items():
            if 'kappa' in run:
                require(services[name].execute(run['id'])['attempt']['accepted'], 'SCHEDULE',
                        'Every admitted lifecycle must be durably accepted by the typed shared prime.')
        checkpoints.append(capture(services, cover, prime, 'accepted'))
        books = {name: export_book(s) for name, s in services.items()}
        tapes = {name: ordinary_tape(book) for name, book in books.items()}
        conformance = all(t['valid'] for t in tapes.values()) and all(c['ledger_compliant'] and
                       c['coverage'] and c['shared_ceiling_compliant'] for c in checkpoints)
        branches[mode] = {'checkpoints': checkpoints, 'books': books, 'admissions': admissions,
            'rail': prime.export(), 'covering': cover.export() if cover else None,
            'ordinary_tapes': tapes, 'ordinary_conformance': conformance,
            'historical_violation': not conformance,
            'intended_breach_detected': mode == 'unsafe' and not conformance}
    return {'schema_version': 1, 'case': 'C10', 'title': 'Two books, one downstream allocation',
            'units': UNITS, 'branches': branches, 'status': 'implemented/unpinned', 'account': ACCOUNT,
            'schedule': ['promises', 'accepted'],
            'topology': 'Two separately persisted local Runtime books, one durable shared prime sink, and an institutional covering allocator in safe modes; trusted same process.',
            'limitations': ['Synthetic one-account order fixture; no production broker or cross-domain netting.',
                'Shared holds survive uncertain local responses; no timeout release. Definite durable local denial can close an unissued hold.',
                'After downstream acceptance, the same lifecycle is idempotent; this fixture does not claim distributed exactly-once execution.',
                'Parent cover and local suballocations are views of the same lifecycles and are not added into E.']}
