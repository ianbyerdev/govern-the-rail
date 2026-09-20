"""Bounded synthetic experiment: a deadline does not prove non-execution.

The two books use the normal Authority, ProtectedSocket and Reconciler. The only
invalid transition is `_unsafe_timeout_release`, restricted to this namespace's
second book and loudly recorded. It is not a receipt, an actor-selectable policy,
or a production reconciliation extension. Durable rail events are the fixture
oracle; they never inform the authority until signed evidence is delivered.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from copy import deepcopy
import fcntl
from pathlib import Path
import sqlite3
from threading import Barrier, RLock

from .domain_models import CancelProposal, DomainPolicy, OrderProposal
from .models import SAACError, require
from .risk_book import decode, encode
from .service import SAACService

EPOCH = 1800000000
UNIT = 100
BUDGET = 'notional_usd_cents'
STAGES = ('admission', 'hidden_fills', 'deadline', 'second_batch', 'recovered_fills', 'cancellations')
LABELS = ('Initial admission', 'Working orders & hidden fills', 'Deadline', 'Second batch', 'Recovered fills', 'Confirmed cancellations')
OFFSETS = (0, 1, 61, 62, 63, 64)
POLICIES = {'evidence': 'Evidence-based reconciliation', 'timeout': 'Release on timeout — deliberately unsafe comparison'}
CONFIG = {'limit_units': 10, 'unit_notional_cents': UNIT, 'original_actors': 100,
          'new_requests': 10, 'hidden_fills': 4, 'capability_ttl_seconds': 30,
          'reconcile_after_seconds': 60, 'reconcile_by': EPOCH + 60,
          'frozen_epoch': EPOCH, 'seed': 0,
          'receipt_order_schedule': 'fills reverse winner request-id order; replay revision 1 then duplicate latest; cancellations reverse order; replay all originals reversed'}
_LOCKS = {}
_LOCKS_GUARD = RLock()


def observe_events(events, now):
    """Pure fixture oracle; no authority risk counters or reservation totals.

    Rail obligation = cumulative executed buy notional + live unfilled limit
    notional. Valid unredeemed promises are reported separately and added to the
    total commitment, never counted again once an order has been created.
    """
    orders, promises, redeemed, closed = {}, {}, set(), set()
    records = []
    for event in events:
        kind, data = event['kind'], event['data']
        order = None
        if kind == 'CAPABILITY_ISSUED' and data['effect']['o'] == 'order.submit':
            promises[event['run_id']] = data
        elif kind == 'EXECUTION_STARTED':
            redeemed.add(event['run_id'])
        elif kind == 'RECEIPT_CREATED' and data['result']['status'] == 'non_use':
            closed.add(event['run_id'])
        elif kind == 'EXECUTION_COMPLETED':
            order = data.get('result', {}).get('order')
        elif kind == 'EMS_FILL':
            order = data
        if order:
            orders[order['id']] = order
            records.append({'source_event_seq': event['seq'], 'source_event_hash': event['hash'],
                            'ts': event['ts'], 'kind': kind, 'order': order,
                            'evidence_class': 'experimental_fixture_oracle_observation'})
    executed = sum(o['notional'] for o in orders.values())
    working = sum((o['quantity'] - o['filled']) * o['limit_price_cents']
                  for o in orders.values() if o['status'] in ('working', 'partial'))
    outstanding = sum(k['reservation']['bound'][BUDGET] for rid, k in promises.items()
                      if rid not in redeemed and rid not in closed and k['iat'] <= now < k['exp'])
    return {'executed_units': executed // UNIT, 'working_units': working // UNIT,
            'obligation_units': (executed + working) // UNIT,
            'outstanding_promises': outstanding // UNIT,
            'total_commitment_units': (executed + working + outstanding) // UNIT,
            'breach_units': max(0, (executed + working + outstanding) // UNIT - CONFIG['limit_units']),
            'modeled_available_units': CONFIG['limit_units'] - (executed + working + outstanding) // UNIT,
            'orders': list(orders.values()), 'records': records,
            'equation': 'rail obligation = cumulative executed notional + remaining live order limit notional; total commitment = rail obligation + valid unredeemed promises',
            'scope': 'Synthetic buy notional only; fixture oracle, not an independent external auditor.'}


class UncertainExperiment:
    """Fixed operator fixture, durable across service reconstruction.

    Stage/action idempotency is recovered from committed requests, orders,
    receipts and events. A process lock protects the pair of books; SQLite
    protects individual admission/redemption transactions. This is a local
    concurrency experiment, not evidence of distributed deployment.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.directory.resolve()), RLock())
        with self._exclusive():
            with self._control() as db:
                db.execute('CREATE TABLE IF NOT EXISTS frames (stage TEXT PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('INSERT OR IGNORE INTO metadata VALUES (1,?)', (encode(CONFIG),))
            self.services = {key: SAACService(self.directory / key, clock=lambda: EPOCH, profile='trading') for key in POLICIES}
            for key, service in self.services.items():
                self._initialize(service, key)

    @contextmanager
    def _exclusive(self):
        with self._lock, (self.directory / 'core.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                for service in getattr(self, 'services', {}).values():
                    with closing(service.book.connect()) as db:
                        service.offset = service.book.state(db).get('clock_offset', 0)
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def _control(self):
        db = sqlite3.connect(self.directory / 'control.sqlite', timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self, service, policy):
        with service.book.transaction() as db:
            state = service.book.state(db)
            if state.get('uncertain_experiment'):
                return
        pack = DomainPolicy(version=2, limits={BUDGET: 10 * UNIT}, per_action={BUDGET: UNIT},
                            resources=['instrument:XYZ/book:CASH-1'], routes=['BROKER-A/session17'],
                            max_ttl_seconds=30, price_min_cents=UNIT, price_max_cents=UNIT)
        # publish() is itself durable; tolerate reconstruction between publication
        # and the experiment marker without publishing a second live pack.
        if state['pack']['version'] == 1:
            service.publish(pack)
        with service.book.transaction() as db:
            state = service.book.state(db)
            state['uncertain_experiment'] = {'policy': policy, 'config': CONFIG}
            service.book.save_state(db, state)
            template = service.book.grant(db, 'G-DEMO')
            for number in range(110):
                service.book.save_grant(db, {**template, 'id': f'G-UNCERTAIN-{number:03}',
                    'agent_id': f'actor.uncertain.{number:03}', 'ceilings': {BUDGET: UNIT}})
            service.book.event(db, None, 'EXPERIMENT_CONFIGURED', {
                'experiment': 'A missing receipt does not create capacity', 'policy': policy,
                'config': CONFIG, 'evidence_class': 'experiment_configuration'}, service.now())

    def _frames(self):
        with self._control() as db:
            frames = {stage: decode(data) for stage, data in db.execute('SELECT stage,data FROM frames')}
        return [frames[stage] for stage in STAGES if stage in frames]

    def _runs(self, service, batch=None):
        with closing(service.book.connect()) as db:
            runs = [decode(r[0]) for r in db.execute('SELECT data FROM runs')]
        if batch is not None:
            runs = [r for r in runs if r.get('request_id', '').startswith(batch + ':')]
        return sorted(runs, key=lambda r: r.get('request_id', ''))

    def _originals(self, service):
        return [r for r in self._runs(service, 'original') if 'kappa' in r]

    def _event_once(self, service, key, kind, data, run_id=None):
        with service.book.transaction() as db:
            if db.execute("SELECT 1 FROM events WHERE kind=? AND json_extract(data,'$.experiment_key')=?", (kind, key)).fetchone():
                return
            service.book.event(db, run_id, kind, {**data, 'experiment_key': key}, service.now())

    def _state(self, service, run, state, reason, next_action):
        data = {'reservation_id': run['reservation_id'], 'state': state, 'reason': reason,
                'next_action': next_action, 'due_at': CONFIG['reconcile_by']}
        with service.book.transaction() as db:
            last = db.execute("SELECT data FROM events WHERE kind='EXPERIMENT_RECONCILIATION_STATE' AND json_extract(data,'$.reservation_id')=? ORDER BY seq DESC LIMIT 1", (run['reservation_id'],)).fetchone()
            if last is None or decode(last[0]) != data:
                service.book.event(db, run['id'], 'EXPERIMENT_RECONCILIATION_STATE', data, service.now())

    def _admit(self, service, batch, count, first_actor=0):
        barrier = Barrier(count)
        existing = {r['request_id']: r for r in self._runs(service, batch)}
        def request(number):
            proposal = OrderProposal(quantity=1, limit_price_cents=UNIT,
                ttl_seconds=CONFIG['capability_ttl_seconds'],
                agent_id=f'actor.uncertain.{first_actor + number:03}',
                grant_id=f'G-UNCERTAIN-{first_actor + number:03}')
            barrier.wait(timeout=30)
            request_id = f'{batch}:{number:03}'
            return existing.get(request_id) or service.authority.propose(proposal, request_id=request_id)
        # Every distinct actor reaches this barrier before any authorization call.
        # No execution/fill tasks run until all admission results have returned.
        with ThreadPoolExecutor(max_workers=count) as pool:
            list(pool.map(request, range(count)))
        self._event_once(service, batch, 'EXPERIMENT_ADMISSION_MEASURED', {
            'batch': batch, 'actors': count, 'barrier_participants': count,
            'execution_held_until_measurement': True,
            'admitted': sum('kappa' in r for r in self._runs(service, batch)),
            'denied': sum('kappa' not in r for r in self._runs(service, batch))})

    def _redeem(self, service, run):
        run = service.get_run(run['id'])
        if not run.get('execution'):
            attempt = service.redeem(run['proposal'], run['kappa'])
            require(attempt['accepted'], 'EXPERIMENT_EXECUTION', 'The scheduled valid capability must redeem.')
        # Recover acceptance evidence from the journal after a restart, without
        # fetching a later withheld fill revision.
        with closing(service.book.connect()) as db:
            receipt = decode(db.execute('SELECT data FROM receipts WHERE reservation_id=? AND revision=1', (run['reservation_id'],)).fetchone()[0])
            row = db.execute('SELECT revision FROM reservations WHERE id=?', (run['reservation_id'],)).fetchone()
        if row['revision'] < 1:
            service.reconciler.accept(receipt)

    def _query(self, service, run, purpose, unavailable=False):
        self._state(service, run, 'in_progress', purpose, 'Query the accepted durable socket journal.')
        self._event_once(service, run['reservation_id'] + ':' + purpose, 'EXPERIMENT_EVIDENCE_QUERIED', {
            'reservation_id': run['reservation_id'], 'source': 'accepted durable socket journal',
            'delivery_gate': 'outcome receipts unavailable' if unavailable else 'available',
            'purpose': purpose}, run['id'])
        if unavailable:
            self._state(service, run, 'unresolved', 'Outcome receipt delivery unavailable; acceptance is not terminal evidence.',
                        'Escalate to operator; retain the commitment and retry evidence recovery.')
            return None
        return service.socket.evidence(run['reservation_id'])

    def _deliver(self, service, run, receipt, purpose):
        try:
            result = service.reconciler.accept(receipt)
        except SAACError as error:
            self._event_once(service, receipt['receipt_id'], 'EXPERIMENT_LATE_RECEIPT_REJECTED', {
                'reservation_id': run['reservation_id'], 'receipt': receipt, 'code': error.code,
                'message': error.message, 'evidence_class': 'signed_institutional_receipt_rejected_by_normal_reconciler'}, run['id'])
            self._state(service, run, 'unresolved', f'Late {purpose} evidence rejected: {error.code}.',
                        'Operator must resolve the invalid timeout release and accounting liability; preserve execution evidence.')
            return False
        current = service.get_run(run['id'])
        if current.get('experiment_invalid_release'):
            self._state(service, run, 'unresolved', 'Timeout release lacks accepted terminal evidence.',
                        'Operator must resolve the invalid release; an older acceptance receipt does not prove non-use.')
        elif current['status'] in ('released', 'settled'):
            self._state(service, run, 'resolved', 'Accepted terminal cumulative evidence.', 'No further release is permitted.')
        else:
            self._state(service, run, 'unresolved', 'Confirmed working order still carries a live commitment.',
                        'Obtain separately authorized cancellation or terminal fill evidence.')
        return result['applied']

    def _unsafe_timeout_release(self, service):
        """Deliberately invalid accounting, isolated to the experiment control.

        No forged non-use receipt is created and no order/nonce/execution is
        erased. Normal reconciliation consequently rejects late evidence when
        it would regress this unsupported release or reopen a terminal record.
        Future admission and redemption still use the unmodified atomic core.
        """
        with service.book.transaction() as db:
            require(service.book.state(db)['uncertain_experiment']['policy'] == 'timeout',
                    'EXPERIMENT_POLICY', 'Invalid timeout release exists only in the negative control.')
            rows = db.execute("SELECT r.* FROM reservations r JOIN runs x ON x.id=r.run_id WHERE json_extract(x.data,'$.request_id') LIKE 'original:%'").fetchall()
            for row in rows:
                if row['status'] == 'released':
                    continue
                before = service.book.risk(db)
                allocation = service.book.allocation(db, row)
                settlement = {'consumed': {k: v['consumed'] for k, v in allocation.items()},
                              'released': {k: v['bound'] - v['consumed'] for k, v in allocation.items()}}
                service.book.settle_dimensions(db, row, settlement)
                db.execute("UPDATE reservations SET released=bound-consumed,status='released' WHERE id=?", (row['id'],))
                run = service.book.run(db, row['run_id'])
                run.update(status='released', experiment_invalid_release=True, risk_after=service.book.risk(db))
                service.book.save_run(db, run)
                service.book.event(db, row['run_id'], 'EXPERIMENT_INVALID_TIMEOUT_RELEASE', {
                    'reservation_id': row['id'], 'risk_before': before, 'risk_after': service.book.risk(db),
                    'evidence_class': 'deliberately_invalid_experimental_accounting_transition',
                    'terminal_evidence': None, 'reason': 'Deadline alone is incorrectly treated as release authority.',
                    'normal_reconciler_bypassed_for_this_transition_only': True}, service.now())

    def _perform(self, stage):
        for policy, service in self.services.items():
            target = EPOCH + OFFSETS[STAGES.index(stage)]
            if service.now() < target:
                service.advance(target - service.now())
            if stage == 'admission':
                self._admit(service, 'original', 100)
            elif stage == 'hidden_fills':
                originals = self._originals(service)
                for run in originals:
                    self._redeem(service, run)
                for run in originals[:4]:
                    current = service.get_run(run['id'])
                    order = current['execution']['result']['order']
                    if order['filled'] == 0:
                        service.socket.fill_order(order['id'], 1, UNIT)
                for run in originals:
                    current = service.get_run(run['id'])
                    if current['status'] != 'uncertain':
                        service.reconciler.uncertain(run['reservation_id'])
            elif stage == 'deadline':
                for run in self._originals(service):
                    self._state(service, run, 'due', 'Reconciliation deadline reached.', 'Start investigation; a timer proves no terminal outcome.')
                    self._query(service, run, 'deadline investigation', unavailable=True)
                if policy == 'timeout':
                    self._unsafe_timeout_release(service)
            elif stage == 'second_batch':
                self._admit(service, 'new', 10, 100)
                for run in self._runs(service, 'new'):
                    if 'kappa' in run:
                        self._redeem(service, run)
            elif stage == 'recovered_fills':
                for run in reversed(self._originals(service)):
                    receipt = self._query(service, run, 'recovered fill' if self._originals(service).index(run) < 4 else 'working status')
                    self._deliver(service, run, receipt, 'fill' if receipt['result']['status'] == 'filled' else 'working status')
                    if receipt['revision'] > 1:
                        # Selected deterministic duplicate/reordered schedule.
                        with closing(service.book.connect()) as db:
                            old = decode(db.execute('SELECT data FROM receipts WHERE reservation_id=? AND revision=1', (run['reservation_id'],)).fetchone()[0])
                        self._deliver(service, run, old, 'reordered acceptance')
                        self._deliver(service, run, receipt, 'duplicate fill')
            elif stage == 'cancellations':
                for run in reversed(self._originals(service)[4:]):
                    current = service.get_run(run['id'])
                    order = current['execution']['result']['order']
                    request_id = 'cancel:' + run['request_id'].split(':')[1]
                    if order['status'] != 'cancelled':
                        proposal = CancelProposal(order_id=order['id'], agent_id=run['proposal']['agent_id'], grant_id=run['proposal']['grant_id'])
                        command = service.authority.propose(proposal, request_id=request_id)
                        require('kappa' in command, 'EXPERIMENT_CANCEL', 'Cancellation requires separately issued authority.')
                        self._redeem(service, command)
                    # A crash may occur after the cancel command committed but
                    # before its own (zero-bound) receipt was accepted.
                    commands = [r for r in self._runs(service, 'cancel') if r['request_id'] == request_id]
                    if commands:
                        self._redeem(service, commands[0])
                    receipt = self._query(service, run, 'confirmed cancellation')
                    self._deliver(service, run, receipt, 'cancellation')
                # Late, repeated and reordered evidence cannot release twice.
                for run in reversed(self._originals(service)):
                    with closing(service.book.connect()) as db:
                        receipts = [decode(r[0]) for r in db.execute('SELECT data FROM receipts WHERE reservation_id=? ORDER BY revision DESC', (run['reservation_id'],))]
                    for receipt in receipts:
                        self._deliver(service, run, receipt, 'replayed terminal' if receipt['revision'] > 1 else 'reordered acceptance')

    def step(self, stage):
        require(stage in STAGES, 'EXPERIMENT_STAGE', 'Unknown fixed experiment stage.')
        with self._exclusive():
            frames = self._frames()
            completed = [f['stage'] for f in frames]
            if stage in completed:
                return self._view(replay=True)
            require(len(completed) < len(STAGES) and STAGES[len(completed)] == stage,
                    'EXPERIMENT_STAGE_ORDER', 'Complete the preceding stage before advancing.')
            self._perform(stage)
            frame = {'stage': stage, 'label': LABELS[STAGES.index(stage)], 'now': EPOCH + OFFSETS[STAGES.index(stage)],
                     'policies': {key: self._panel(key, service) for key, service in self.services.items()}}
            with self._control() as db:
                db.execute('INSERT INTO frames VALUES (?,?)', (stage, encode(frame)))
            return self._view()

    def run(self):
        result = None
        for stage in STAGES:
            result = self.step(stage)
        return result

    def _panel(self, policy, service):
        with service.book.transaction() as db:
            events = service.book.events(db)
            risk = service.book.risk(db)['budgets'][BUDGET]
            reservations = {r['id']: dict(r) for r in db.execute('SELECT * FROM reservations')}
            journal = [decode(r[0]) for r in db.execute('SELECT data FROM receipts')]
        oracle = observe_events(events, service.now())
        oracle.pop('orders')
        oracle.pop('records')
        oracle['unaccounted_units'] = oracle['total_commitment_units'] - (risk['used'] + risk['reserved']) // UNIT
        states = {}
        for event in events:
            if event['kind'] == 'EXPERIMENT_RECONCILIATION_STATE':
                states[event['data']['reservation_id']] = event['data']
        counts = {state: sum(v['state'] == state for v in states.values()) for state in ('due', 'in_progress', 'unresolved', 'resolved')}
        rejected = [e['data'] for e in events if e['kind'] == 'EXPERIMENT_LATE_RECEIPT_REJECTED']
        delivered_rejections = {r['receipt']['receipt_id'] for r in rejected}
        withheld = {r['reservation_id'] for r in journal if r['revision'] > reservations[r['reservation_id']]['revision'] and r['receipt_id'] not in delivered_rejections}
        orders = observe_events(events, service.now())['orders']
        admissions = {}
        for batch, key in (('original', 'original'), ('new', 'new')):
            runs = self._runs(service, batch)
            admissions[key] = {'requested': len(runs), 'admitted': sum('kappa' in r for r in runs), 'denied': sum('kappa' not in r for r in runs)}
        return {'id': policy, 'label': POLICIES[policy],
                'event_seq': events[-1]['seq'] if events else 0, 'event_head': events[-1]['hash'] if events else 'genesis',
                'book': {'limit': risk['limit'] // UNIT, 'consumed': risk['used'] // UNIT,
                         'reserved': risk['reserved'] // UNIT, 'available': risk['available'] // UNIT},
                'admission': admissions, 'rail': {
                    'working_orders': sum(o['status'] in ('working', 'partial') for o in orders),
                    'filled_orders': sum(o['status'] == 'filled' for o in orders),
                    'cancelled_orders': sum(o['status'] == 'cancelled' for o in orders),
                    'executed_units': oracle['executed_units'], 'withheld_receipts': len(withheld),
                    'accepted_evidence': sum(e['kind'] == 'RECEIPT_RECONCILED' for e in events)},
                'observer': oracle, 'reconciliation': {**counts,
                    'state': 'unresolved' if counts['unresolved'] else 'in_progress' if counts['in_progress'] else 'due' if counts['due'] else 'resolved' if counts['resolved'] else 'waiting',
                    'investigations_started': sum(e['kind'] == 'EXPERIMENT_EVIDENCE_QUERIED' for e in events),
                    'deadlines_reached': sum(e['kind'] == 'EXPERIMENT_RECONCILIATION_STATE' and e['data']['state'] == 'due' for e in events),
                    'history': [{'ts': e['ts'], **e['data']} for e in events if e['kind'] == 'EXPERIMENT_RECONCILIATION_STATE'],
                    'exceptions': [{**v, 'age_seconds': service.now() - v['due_at']} for v in states.values() if v['state'] == 'unresolved']},
                'invalid_transitions': sum(e['kind'] == 'EXPERIMENT_INVALID_TIMEOUT_RELEASE' for e in events),
                'receipt_rejections': rejected,
                'evidence_class': 'real_core_book' if policy == 'evidence' else 'real_core_book_with_explicitly_invalid_experimental_release'}

    def _view(self, replay=False):
        frames = self._frames()
        return {'schema_version': 1, 'title': 'A missing receipt does not create capacity',
                'stage': frames[-1]['stage'] if frames else 'ready', 'completed_stages': [f['stage'] for f in frames],
                'now': max(s.now() for s in self.services.values()), 'config': deepcopy(CONFIG),
                'policies': {key: self._panel(key, service) for key, service in self.services.items()},
                'frames': frames, 'timeline': [{k: f[k] for k in ('stage', 'label', 'now')} for f in frames],
                'replay': replay, 'synthetic_only': True,
                'explanation': 'Both paths reserve atomically. A deadline starts reconciliation; it does not prove that an order never executed.'}

    def view(self):
        with self._exclusive():
            return self._view()

    def export(self):
        with self._exclusive():
            books = {}
            for key, service in self.services.items():
                with service.book.transaction() as db:
                    book = {'events': service.book.events(db), 'keys': {'riskbook-1': service.issuer.public, 'socket-1': service.socket_signer.public}}
                    for table in ('runs', 'orders', 'receipts', 'executions', 'packs', 'grants'):
                        book[table] = [decode(r[0]) for r in db.execute(f'SELECT data FROM {table}')]
                    book['reservations'] = [{**dict(r), 'kappa': decode(r['kappa'])} for r in db.execute('SELECT * FROM reservations')]
                    book['allocations'] = {r['reservation_id']: decode(r['data']) for r in db.execute('SELECT * FROM allocations')}
                    book['nonces'] = [dict(r) for r in db.execute('SELECT * FROM nonces')]
                book['observer'] = observe_events(book['events'], service.now())
                books[key] = book
            view = self._view()
            return {'schema_version': 1, 'experiment': view['title'], 'config': deepcopy(CONFIG),
                    'schedule': [{'stage': stage, 'virtual_time': EPOCH + OFFSETS[i], 'label': LABELS[i]} for i, stage in enumerate(STAGES)],
                    'view': view, 'frames': view['frames'], 'books': books,
                    'trust': {'oracle': 'Fixture oracle derived from durable rail events, not an independent external auditor; hidden events do not update authority counters.',
                              'negative_control': 'Only timeout release bypasses reconciliation in its isolated book. Admission, signatures, exact effects, nonce checks and order execution use the unchanged core. No non-use receipt is fabricated.',
                              'restart': 'Durable SQLite journals and stage checkpoints support service reconstruction; stage actions recover by request identity and durable order/receipt state.',
                              'units': 'One displayed unit is 100 integer USD notional cents: one synthetic share at a 100-cent buy limit; no portfolio netting or market risk claim.'}}
