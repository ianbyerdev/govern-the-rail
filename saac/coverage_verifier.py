"""Offline reconstruction of C8–C10 evidence, independent of dashboard/oracles.

The verifier reads public artifacts only. Locally rooted keys and histories are
not external attestations; replacing the entire unanchored history is outside
this verifier's guarantee. Expected tables are compared after reconstruction.
"""
from pathlib import Path
import json

from .accounting import settlement
from .crypto import digest, verify
from .models import SAACError
from .packs import evaluate
from .protocol import verify_artifact
from .tape import verify_tape

BUDGET = 'notional_usd_cents'
EXPECTED = Path(__file__).resolve().parent.parent / 'docs/research/coverage/expected-checkpoints.json'


class EvidenceError(ValueError):
    pass


def check(value, message):
    if not value:
        raise EvidenceError(message)


def same(actual, expected, message):
    if isinstance(actual, set):
        actual = sorted(actual)
    if isinstance(expected, set):
        expected = sorted(expected)
    check(digest(actual) == digest(expected), message)


def index(rows, key):
    result = {row[key]: row for row in rows}
    check(len(result) == len(rows), f'Duplicate {key} in evidence')
    return result


def attest(snapshot, kid, artifact_type='coverage-book-checkpoint'):
    """Authenticate a fixture snapshot, never authorize institutional release."""
    statement = snapshot['attestation']
    verify(statement, snapshot['keys'][kid], kid, artifact_type)
    same(statement['snapshot_digest'], digest({k: v for k, v in snapshot.items() if k != 'attestation'}),
         'Signed fixture snapshot differs from exported history/projections')
    same(statement['head'], snapshot['head'], 'Signed fixture head differs')


def chain(events, head=None):
    previous = 'genesis'
    for number, event in enumerate(events, 1):
        same(event['seq'], number, 'Missing or replayed event sequence')
        same(event['prev_hash'], previous, 'Broken event predecessor')
        same(event['hash'], digest({k: v for k, v in event.items() if k != 'hash'}), 'Changed event hash')
        check(type(event['ts']) is int, 'Noninteger event clock')
        previous = event['hash']
    if head is not None:
        same(previous, head, 'History does not reach retained checkpoint')
    return previous


def ordinary(book):
    try:
        return verify_tape(book['events'], book['keys'], book.get('head'))
    except SAACError as error:
        return {'valid': False, 'code': error.code, 'message': error.message}


def replay_book(book, allow_invalid=False, remote_receipts=None):
    """Rebuild allocation from events and bind all receipt/order projections."""
    attest(book, 'riskbook-1')
    events, keys = book['events'], book['keys']
    chain(events, book.get('head'))
    runs = index(book['runs'], 'id')
    reservations, capabilities, nonces, receipts, executions, orders = {}, {}, {}, {}, {}, {}
    proposals, invalid, accepted, rejections = {}, [], [], []

    def totals():
        return (sum(r['consumed'] for r in reservations.values()),
                sum(r['bound']-r['consumed']-r['released'] for r in reservations.values()))

    def risk_matches(risk):
        used, reserved = totals()
        v = risk['budgets'][BUDGET]
        same([v['used'], v['reserved'], v['available']],
             [used, reserved, v['limit']-used-reserved], 'Snapshot/policy counters differ from reconstructed allocation')

    for event in events:
        kind, data, rid = event['kind'], event['data'], event['run_id']
        if kind == 'PROPOSAL_CREATED':
            check(rid not in proposals, 'Replayed proposal creation')
            proposals[rid] = data
            same(runs[rid]['proposal'], data, 'Proposal snapshot was forged')
        elif kind == 'POLICY_EVALUATED':
            verify_artifact(data['inputs']['pack'], keys['riskbook-1'], 'riskbook-1', 'saac-pack')
            same(evaluate(**data['inputs']), data['decision'], 'Policy verdict does not replay')
            risk_matches(data['inputs']['risk'])
            same(data['decision'], runs[rid]['decision'], 'Admission verdict differs from its event')
        elif kind == 'AUTHORITY_DENIED':
            same(runs[rid]['decision'], data, 'Denied admission was forged')
        elif kind == 'RISK_RESERVED':
            risk_matches(data['risk_before'])
            check(data['id'] not in reservations, 'Reservation identity reused')
            bound = data['bound'][BUDGET]
            check(type(bound) is int and 0 <= bound <= data['risk_before']['budgets'][BUDGET]['available'],
                  'Allocation exceeds authoritative capacity')
            reservations[data['id']] = {'bound': bound, 'consumed': 0, 'released': 0,
                'revision': 0, 'status': 'reserved', 'receipt_hash': None, 'run_id': rid, 'seq': event['seq']}
        elif kind == 'CAPABILITY_ISSUED':
            verify_artifact(data, keys['riskbook-1'], 'riskbook-1', 'saac-kappa')
            reservation = data['reservation']['id']
            row = reservations[reservation]
            check(reservation not in capabilities and data['nonce'] not in nonces, 'Duplicate usable authority')
            same(data['reservation']['bound'], {BUDGET: row['bound']}, 'Capability allocation binding differs')
            same(data['reservation']['book_seq'], row['seq'], 'Authority precedes reservation')
            same(row['run_id'], rid, 'Changed capability lifecycle')
            same(data, runs[rid]['kappa'], 'Capability projection differs')
            same(data['effect_digest'], digest(data['effect']), 'Effect digest differs')
            same(data['snapshot_hash'], digest(runs[rid]['snapshot']), 'Snapshot binding differs')
            for field in ('agent_id', 'grant_id', 'principal_id'):
                same(data[field], proposals[rid][field], 'Authority identity differs')
            same(data['aud'], proposals[rid]['socket_id'], 'Authority audience differs')
            capabilities[reservation] = data
            nonces[data['nonce']] = {'nonce': data['nonce'], 'status': 'unused', 'execution_id': None}
        elif kind == 'EXECUTION_STARTED':
            kappa = runs[rid]['kappa']
            nonce = nonces[kappa['nonce']]
            same(nonce['status'], 'unused', 'Protected effect executed twice')
            same(data['effect'], kappa['effect'], 'Executed effect binding differs')
            check(kappa['iat'] <= event['ts'] < kappa['exp'], 'Expired authority executed')
            nonce.update(status='consumed', execution_id=data['execution_id'])
            reservations[kappa['reservation']['id']]['status'] = 'executing'
        elif kind in ('REMOTE_REDEMPTION_CLAIMED', 'REMOTE_NON_USE_CLAIMED'):
            check(remote_receipts is not None, 'Remote claim in a local-only book')
            kappa = runs[rid]['kappa']
            nonce = nonces[kappa['nonce']]
            same(data['kappa_id'], kappa['kappa_id'], 'Remote claim capability differs')
            same(data['nonce'], kappa['nonce'], 'Remote claim nonce differs')
            same(data['effect_digest'], kappa['effect_digest'], 'Remote claim effect differs')
            check(nonce['status'] in ('unused', 'remote_claimed'), 'Remote authority already closed')
            if nonce['execution_id'] is not None:
                same(data['execution_id'], nonce['execution_id'], 'Remote retry changed execution identity')
            nonce.update(status='closed' if kind == 'REMOTE_NON_USE_CLAIMED' else 'remote_claimed',
                         execution_id=data['execution_id'])
            reservations[kappa['reservation']['id']]['status'] = 'executing'
        elif kind == 'REMOTE_EVIDENCE_DELIVERED':
            check(remote_receipts is not None, 'Undeclared remote evidence source')
            rho = data['receipt']
            same(rho, remote_receipts[rho['receipt_id']], 'Delivered evidence is absent from B signed journal')
            kappa = capabilities[rho['reservation_id']]
            for field in ('snapshot_hash', 'kappa_id', 'nonce', 'aud'):
                same(rho[field], kappa[field], 'Remote receipt lifecycle changed')
            same(nonces[kappa['nonce']]['execution_id'], rho['execution_id'], 'Remote evidence has no A claim')
            check(rho['receipt_id'] not in receipts, 'Repeated remote evidence import')
            receipts[rho['receipt_id']] = rho
        elif kind == 'RECEIPT_CREATED':
            verify_artifact(data, keys['socket-1'], 'socket-1', 'saac-receipt')
            kappa = capabilities[data['reservation_id']]
            same(reservations[data['reservation_id']]['run_id'], rid, 'Receipt event lifecycle differs')
            for field in ('snapshot_hash', 'kappa_id', 'nonce', 'aud'):
                same(data[field], kappa[field], 'Changed signed receipt binding')
            check(data['receipt_id'] not in receipts, 'Replayed receipt creation')
            same(data['settlement'], settlement(kappa['reservation']['bound'], data['result']['consumed'],
                                               data['result']['released']), 'Signed evidence violates conservation')
            nonce = nonces[kappa['nonce']]
            if data['result']['status'] == 'non_use':
                same(nonce['status'], 'unused', 'Non-use after execution claim')
                nonce.update(status='closed', execution_id=data['execution_id'])
            same(nonce['execution_id'], data['execution_id'], 'Receipt lacks durable redemption')
            prior = executions.get(data['execution_id'])
            same(data['revision'], prior['revision']+1 if prior else 1, 'Missing or replayed cumulative revision')
            if prior:
                for field in ('consumed', 'released'):
                    check(data['result'][field][BUDGET] >= prior['result'][field][BUDGET], 'Cumulative outcome regressed')
            receipts[data['receipt_id']] = data
            executions[data['execution_id']] = {'id': data['execution_id'], 'revision': data['revision'], 'result': data['result']}
            order = data['result'].get('order')
            if order:
                check(type(order['filled']) is int and 0 <= order['filled'] <= order['quantity'], 'Invalid filled quantity')
                same(order['notional'], order['filled']*order['limit_price_cents'], 'Synthetic fill notional differs')
                if kappa['effect']['o'] == 'order.submit':
                    same(order['execution_id'], data['execution_id'], 'Order lifecycle binding changed')
                    same(order['quantity']*order['limit_price_cents'], kappa['reservation']['bound'][BUDGET], 'Order exceeds issued bound')
                prior_order = orders.get(order['id'])
                if prior_order:
                    check(order['version'] >= prior_order['version'], 'Rail order version regressed')
                    if order['version'] == prior_order['version']:
                        same(order, prior_order, 'Same-version rail order changed')
                orders[order['id']] = order
        elif kind == 'RECEIPT_RECONCILED':
            rho = data['receipt']
            same(receipts[rho['receipt_id']], rho, 'Runtime accepted evidence absent from signed journal')
            row = reservations[rho['reservation_id']]
            risk_matches(data['risk_before'])
            check(row['status'] not in ('released', 'settled'), 'Terminal reservation reopened')
            check(rho['revision'] > row['revision'], 'Applied evidence did not advance')
            s = rho['settlement']
            check(s['consumed'][BUDGET] >= row['consumed'] and s['released'][BUDGET] >= row['released'],
                  'Runtime accepted regressing evidence')
            row.update(consumed=s['consumed'][BUDGET], released=s['released'][BUDGET],
                       revision=rho['revision'], receipt_hash=digest(rho), status=s['reservation_status'])
            risk_matches(data['risk_after'])
            accepted.append(rho)
        elif kind == 'RECEIPT_DUPLICATE':
            rho = receipts[data['receipt_id']]
            check(rho['revision'] <= reservations[rho['reservation_id']]['revision'], 'Unaccepted duplicate evidence')
        elif kind == 'RECONCILIATION_REQUIRED':
            reservations[data['id']]['status'] = 'uncertain'
        elif kind in ('EXPERIMENT_INVALID_REVOKE_RELEASE', 'UNSAFE_TIMEOUT_RELEASE'):
            check(allow_invalid, 'Unsafe release appeared in a safe branch')
            if kind == 'UNSAFE_TIMEOUT_RELEASE':
                same(data['unsupported_by_evidence'], True, 'Undeclared unsupported release')
            else:
                same(data['terminal_evidence'], None, 'Unsupported release fabricated accepted evidence')
                same(data['normal_reconciler_bypassed_for_this_transition_only'], True, 'Undeclared negative control')
            risk_matches(data['risk_before'])
            row = reservations[data['reservation_id']]
            row.update(released=row['bound']-row['consumed'], status='released')
            risk_matches(data['risk_after'])
            invalid.append(event)
        elif kind in ('LATE_EVIDENCE_REJECTED', 'REMOTE_EVIDENCE_REJECTED'):
            rho = data['receipt']
            same(receipts[rho['receipt_id']], rho, 'Rejected evidence absent from journal')
            check(data['code'] in ('RECEIPT_REGRESSION', 'RECEIPT_TERMINAL'), 'Unexpected late-evidence rejection')
            check(allow_invalid and reservations[rho['reservation_id']]['status'] == 'released', 'Invalid late rejection')
            rejections.append(data)
    same(set(runs), set(proposals), 'Missing or injected admission')
    # These projections can be forged without touching the event chain; rebuild each.
    same(index(book['receipts'], 'receipt_id'), receipts, 'Receipt history projection differs')
    same(index(book['executions'], 'id'), executions, 'Execution projection differs')
    same(index(book['orders'], 'id'), orders, 'Raw rail records differ from signed outcomes')
    same(index(book['nonces'], 'nonce'), nonces, 'Durable nonce projection differs')
    raw_reservations = index(book['reservations'], 'id')
    same(set(raw_reservations), set(reservations), 'Missing or injected reservation')
    same(set(book['allocations']), set(reservations), 'Allocation set differs')
    for key, row in reservations.items():
        for field in ('bound', 'consumed', 'released', 'revision', 'status', 'receipt_hash', 'run_id'):
            same(raw_reservations[key][field], row[field], 'Forged reservation snapshot: '+field)
        same(raw_reservations[key]['kappa'], capabilities[key], 'Reservation capability differs')
        same(book['allocations'][key], {BUDGET: {f: row[f] for f in ('bound', 'consumed', 'released')}}, 'Forged allocation snapshot')
    for event in events:
        if event['kind'] == 'EXECUTION_COMPLETED':
            execution = event['data']
            check(any(r['execution_id'] == execution['id'] and r['revision'] == execution['revision']
                      and r['result'] == execution['result'] for r in receipts.values()), 'Execution lacks matching signed outcome')
        elif event['kind'] == 'EMS_FILL':
            check(any(r['result'].get('order') == event['data'] for r in receipts.values()), 'Fill lacks signed evidence')
    verify_artifact(book['state']['pack'], keys['riskbook-1'], 'riskbook-1', 'saac-pack')
    same(book['state']['pack_hash'], digest(book['state']['pack']), 'Current policy hash differs')
    risk_matches(book['risk'])
    same(book['risk']['budgets'][BUDGET]['limit'], book['state']['pack']['limits'][BUDGET], 'Limit differs from signed policy')
    return {'U': totals()[0], 'Q': totals()[1], 'L': book['risk']['budgets'][BUDGET]['limit'],
            'capabilities': capabilities, 'receipts': receipts, 'invalid': invalid,
            'accepted': accepted, 'rejections': rejections, 'orders': orders}


def obligation(capabilities, receipts, now):
    """Count each issued order lifecycle once, replacing promises on acceptance."""
    latest = {}
    for rho in receipts.values():
        key = rho['kappa_id']
        if key not in latest or rho['revision'] > latest[key]['revision']:
            latest[key] = rho
    amount, promises, promise_amount, lifecycles = 0, 0, 0, []
    for kappa in capabilities.values():
        if kappa['effect']['o'] != 'order.submit':
            continue
        rho = latest.get(kappa['kappa_id'])
        consumed, residual = 0, 0
        if rho:
            if rho['result']['status'] == 'non_use':
                domain = 'accepted_non_use'
            else:
                order = rho['result']['order']
                consumed = order['notional']
                if order['status'] in ('working', 'partial'):
                    residual = (order['quantity']-order['filled'])*order['limit_price_cents']
                domain = 'accepted_rail_execution'
        elif kappa['iat'] <= now < kappa['exp']:
            residual = kappa['reservation']['bound'][BUDGET]
            promise_amount += kappa['reservation']['bound'][BUDGET]
            promises += 1
            domain = 'usable_unredeemed_promise'
        else:
            domain = 'expired_unredeemed_promise'
        amount += consumed+residual
        lifecycles.append({'kappa_id': kappa['kappa_id'], 'reservation_id': kappa['reservation']['id'],
                           'execution_id': rho['execution_id'] if rho else None,
                           'consumed': consumed, 'residual': residual, 'E': consumed+residual, 'evidence_domain': domain})
    return {'E': amount, 'outstanding_promises': promises, 'promise_obligation': promise_amount,
            'lifecycles': lifecycles,
            'evidence_domain': 'issuance_joined_with_rail' if promises else 'rail_execution_journal'}


def checkpoint_fields(checkpoint, state, oracle, shared_limit=None):
    u, q, limit = state['U'], state['Q'], state['L']
    expected = {'U': u, 'Q': q, 'L': limit, 'E': oracle['E'], 'available': limit-u-q,
                'coverage': oracle['E'] <= u+q, 'ledger_compliant': u+q <= limit,
                'shared_ceiling_compliant': oracle['E'] <= (shared_limit if shared_limit is not None else limit),
                'outstanding_promises': oracle['outstanding_promises'],
                'evidence_domain': oracle['evidence_domain']}
    for field, value in expected.items():
        same(checkpoint[field], value, 'Forged observed checkpoint: '+field)
    if 'coverage_gap' in checkpoint:
        same(checkpoint['coverage_gap'], oracle['E']-u-q, 'Coverage gap was clipped or changed')
    if 'oracle' in checkpoint:
        for field in ('E', 'outstanding_promises', 'promise_obligation', 'evidence_domain'):
            same(checkpoint['oracle'][field], oracle[field], 'Exported oracle differs: '+field)
        same(checkpoint['oracle']['oracle_is_accepted_evidence'], False, 'Evaluator masquerades as accepted evidence')
        if 'lifecycles' in oracle:
            same(checkpoint['oracle']['lifecycles'], oracle['lifecycles'], 'Oracle lifecycle reconstruction differs')
    return expected


def _verify_c9(bundle, expected):
    conformance, comparisons = {}, []
    same(set(bundle['branches']), {'safe', 'unsafe'}, 'Wrong C9 branches')
    for name, branch in bundle['branches'].items():
        checkpoints = branch['checkpoints']
        same([c['id'] for c in checkpoints], list(expected['C9']), 'Missing/reordered C9 checkpoint')
        prior_events = []
        observed = []
        for frame in checkpoints:
            book = frame['snapshot']
            same(book['events'][:len(prior_events)], prior_events, 'Earlier checkpoint history was replaced')
            prior_events = book['events']
            state = replay_book(book, name == 'unsafe')
            oracle = obligation(state['capabilities'], state['receipts'], frame['now'])
            fields = checkpoint_fields(frame, state, oracle)
            target = [v*100 for v in expected['C9'][frame['id']][name]]
            values = [fields[k] for k in ('U', 'Q', 'E')]
            same(values, target, 'Observed C9 checkpoint differs from expected-only schedule')
            comparisons.append({'branch': name, 'checkpoint': frame['id'], 'expected': target, 'observed': values, 'matches': True})
            observed.append(fields)
        same(branch['book'], checkpoints[-1]['snapshot'], 'Final C9 export differs from recorded checkpoint')
        same(branch['admissions'], branch['book']['runs'], 'Admission export differs')
        state = replay_book(branch['book'], name == 'unsafe')
        runs = index(branch['book']['runs'], 'id')
        for field in ('revoked_new_request', 'competitor', 'cleanup'):
            projection = branch[field]
            original = runs[projection['id']]
            for key in ('proposal', 'request_id', 'decision', 'kappa'):
                same(projection.get(key), original.get(key), 'C9 admission/cleanup projection differs: '+field)
        scope_events = [e['data'] for e in branch['book']['events'] if e['kind'] == 'COMPETITOR_SCOPE_CHECKED']
        same(len(scope_events), 1, 'Competitor scope check missing')
        same(branch['competitor_scope_check'], scope_events[0]['policy_with_zero_occupancy'], 'Competitor scope projection differs')
        for delivery in branch['late_evidence']:
            rho = delivery['receipt']
            same(rho, state['receipts'][rho['receipt_id']], 'Late evidence projection differs from signed receipt')
            if delivery['accepted']:
                matches = [e['data'] for e in branch['book']['events']
                           if e['kind'] == 'RECEIPT_RECONCILED' and e['data']['receipt'] == rho]
                same(len(matches), 1, 'Reported accepted evidence has no reconciliation event')
                same(delivery['result'], {'applied': True, 'risk': matches[0]['risk_after']}, 'Late accepted result differs')
            else:
                check(delivery in state['rejections'], 'Reported rejection differs from normal reconciler history')
        grants = {g['id']: g for g in branch['book']['grants']}
        check(not grants['G-DEMO']['active'], 'Parent was not revoked')
        for gid in ('G-COMPETITOR', 'G-CLEANUP'):
            check(grants[gid]['active'] and grants[gid]['parent_grant_id'] is None and not grants[gid]['chain'], 'Competitor/cleanup belongs to revoked subtree')
        same(branch['revoked_new_request']['decision']['code'], 'GRANT_INACTIVE', 'Revoked lineage was not rejected')
        same(branch['competitor_scope_check']['verdict'], 'allow', 'Competitor failed non-capacity checks')
        same(branch['competitor']['decision']['code'], 'SESSION_LIMIT' if name == 'safe' else 'POLICY_ALLOW', 'Competitor denial was not capacity-specific')
        if name == 'unsafe':
            check(len(state['invalid']) == 1 and len(state['rejections']) == 2, 'Required invalid release/late rejection missing')
            check(all(not value['accepted'] for value in branch['late_evidence']), 'Mandatory late rejection was repaired')
        normal = ordinary(branch['book'])
        same(branch['ordinary_tape'], normal, 'Ordinary tape projection differs')
        valid = normal['valid'] and all(c['coverage'] and c['ledger_compliant'] and c['shared_ceiling_compliant'] for c in observed)
        same(branch['ordinary_conformance'], valid, 'Ordinary conformance label differs')
        same(branch['historical_violation'], name == 'unsafe', 'Historical failure was erased')
        same(branch['intended_breach_detected'], name == 'unsafe', 'Intended breach detection label differs')
        conformance[name] = {'valid': valid, 'ordinary_tape': normal}
    return conformance, comparisons


def replay_cover(cover, books, mode):
    """Signed shared-allocation history must conserve the real account rights."""
    attest(cover, 'cover-1', 'coverage-cover-checkpoint')
    public = cover['keys']['cover-1']
    config = cover['config']
    verify(config, public, 'cover-1', 'coverage-cover-config')
    same(config['account']['L_star'], 1000, 'True shared ceiling was enlarged')
    same(config['mode'], mode, 'Cover mode differs')
    split = config['split']
    same(split, {'A': 600, 'B': 400} if mode == 'split' else None, 'Split was not conserved')
    holds, previous = {}, 'genesis'
    for seq, event in enumerate(cover['events'], 1):
        verify(event, public, 'cover-1', 'coverage-cover-event')
        same(event['seq'], seq, 'Missing covering event')
        same(event['prev_hash'], previous, 'Broken covering event chain')
        previous = digest(event)
        kind, data = event['kind'], event['data']
        if kind == 'COVER_CONFIGURED':
            same(data, config, 'Cover config differs from history')
            same(seq, 1, 'Cover configuration changed')
        elif kind in ('COVER_HELD', 'COVER_DENIED'):
            check(data['id'] not in holds, 'Covering identity was reused')
            check(type(data['bound']) is int and data['bound'] > 0, 'Invalid covering amount')
            occupied = sum(h['bound'] for h in holds.values() if h['status'] in ('held', 'issued'))
            local = sum(h['bound'] for h in holds.values() if h['status'] in ('held', 'issued') and h['book_id'] == data['book_id'])
            right = split[data['book_id']] if split else 1000
            same(data['available_before'], 1000-occupied, 'Cover headroom differs')
            same(data['assigned_right'], right, 'Cover assigned rights differ')
            allowed = occupied+data['bound'] <= 1000 and local+data['bound'] <= right
            same(kind, 'COVER_HELD' if allowed else 'COVER_DENIED', 'Cover admission does not replay')
            same(data['status'], 'held' if allowed else 'denied', 'Cover status differs')
            same(data['kappa_id'], None, 'Cover was acquired after usable authority')
            holds[data['id']] = data.copy()
        elif kind in ('LOCAL_ISSUANCE_LINKED', 'UNISSUED_COVER_RELEASED'):
            hold, run = data['hold'], data['local_result']
            prior = holds[hold['id']]
            check(prior['status'] in ('held', 'issued'), 'Local result has no prior covering allocation')
            for field in ('id', 'book_id', 'request_id', 'intent', 'bound', 'account_id'):
                same(hold[field], prior[field], 'Covering lifecycle retargeted')
            same(run['request_id'], hold['request_id'], 'Cover request binding differs')
            local_run = index(books[hold['book_id']]['runs'], 'id')[run['id']]
            same(run['proposal'], local_run['proposal'], 'Cover local intent differs')
            if kind == 'LOCAL_ISSUANCE_LINKED':
                same(run['kappa'], local_run['kappa'], 'Cover bound to invented capability')
                same(hold['kappa_id'], run['kappa']['kappa_id'], 'Cover capability binding differs')
                same(hold['bound'], run['kappa']['reservation']['bound'][BUDGET], 'Cover amount differs')
                same(hold['status'], 'issued', 'Usable authority lacks held cover')
            else:
                check('kappa' not in run and run['status'] == 'denied' and prior['kappa_id'] is None,
                      'Cover released after ambiguous/usable issuance')
                same(hold['status'], 'released', 'Definite denial did not close hold')
            holds[hold['id']] = hold.copy()
        elif kind == 'COVER_RETRIED':
            same(data['status'], holds[data['hold_id']]['status'], 'Retry altered covering rights')
        else:
            raise EvidenceError('Unknown covering transition')
    same(cover['head'], previous, 'Cover history checkpoint differs')
    same(index(cover['holds'], 'id'), holds, 'Forged covering snapshot')
    occupied = sum(h['bound'] for h in holds.values() if h['status'] in ('held', 'issued'))
    same(cover['Q'], occupied, 'Cover charge differs')
    check(occupied <= 1000, 'Cover allocation exceeds true limit')
    return holds


def _verify_c10(bundle, expected):
    same(set(bundle['branches']), {'unsafe', 'covering', 'split'}, 'Wrong C10 configurations')
    same(bundle['account']['L_star'], 1000, 'Shared ceiling differs from specification')
    conformance, comparisons = {}, []
    for mode, branch in bundle['branches'].items():
        same([c['id'] for c in branch['checkpoints']], ['promises', 'accepted'], 'Missing C10 checkpoint')
        prior_books, prior_prime, observed = {}, [], []
        for frame in branch['checkpoints']:
            snapshot = frame['snapshot']
            books, rail, cover = snapshot['books'], snapshot['rail'], snapshot['covering']
            same(set(books), {'A', 'B'}, 'C10 local book missing')
            states = {name: replay_book(book) for name, book in books.items()}
            caps = {name+':'+cap['kappa_id']: cap for name, state in states.items() for cap in state['capabilities'].values()}
            for name, book in books.items():
                prior = prior_books.get(name, [])
                same(book['events'][:len(prior)], prior, 'Local history changed across checkpoints')
                prior_books[name] = book['events']
            public = rail['keys']['prime-1']
            attest(rail, 'prime-1', 'coverage-prime-checkpoint')
            verify(rail['config'], public, 'prime-1', 'coverage-prime-config')
            same(rail['config']['account'], bundle['account'], 'Downstream account scope differs')
            same(rail['config']['mode'], mode, 'Downstream control mode differs')
            same(rail['config']['issuer_keys'], {n: b['keys']['riskbook-1'] for n, b in books.items()}, 'Downstream issuer binding differs')
            same(rail['records'][:len(prior_prime)], prior_prime, 'Downstream acceptance history changed')
            prior_prime = rail['records']
            if mode == 'unsafe':
                same(cover, None, 'Unsafe control unexpectedly has common allocation')
                holds = {}
            else:
                holds = replay_cover(cover, books, mode)
                for lifecycle, cap in caps.items():
                    check(any(h['kappa_id'] == cap['kappa_id'] and h['book_id'] == lifecycle.split(':')[0]
                              and h['status'] == 'issued' and h['bound'] == cap['reservation']['bound'][BUDGET]
                              for h in holds.values()), 'Locally usable authority has no preceding common cover')
            same(frame['covering'], cover, 'Displayed cover differs')
            accepted, previous, rail_amount = {}, 'genesis', 0
            for seq, record in enumerate(rail['records'], 1):
                verify(record, public, 'prime-1', 'coverage-prime-acceptance')
                same(record['seq'], seq, 'Missing or replayed downstream acceptance')
                same(record['prev_hash'], previous, 'Broken downstream acceptance history')
                previous = digest(record)
                lifecycle = record['lifecycle']
                check(lifecycle not in accepted, 'Duplicate downstream protected effect')
                cap = caps[lifecycle]
                same(record['capability'], cap, 'Downstream authority differs from issuance')
                same(lifecycle, record['book_id']+':'+cap['kappa_id'], 'Downstream lifecycle retargeted')
                same(record['effect'], cap['effect'], 'Downstream executed effect differs')
                same(record['bound'], cap['reservation']['bound'][BUDGET], 'Downstream bound differs')
                same(record['account_id'], bundle['account']['id'], 'Downstream account binding changed')
                order = record['order']
                local_order = states[record['book_id']]['orders'][order['id']]
                same({k: v for k, v in order.items() if k != 'execution_id'},
                     {k: v for k, v in local_order.items() if k != 'execution_id'}, 'Downstream order differs from signed local outcome')
                local_receipts = [r for r in states[record['book_id']]['receipts'].values() if r['kappa_id'] == cap['kappa_id']]
                check(any(r['result'].get('prime_acceptance_hash') == digest(record) and
                          r['result'].get('prime_execution_id') == order['execution_id'] for r in local_receipts),
                      'Local signed outcome does not bind the durable downstream acceptance')
                if mode != 'unsafe':
                    hold = holds[record['covering_hold_id']]
                    same(hold['kappa_id'], cap['kappa_id'], 'Downstream cover binds different lifecycle')
                else:
                    same(record['covering_hold_id'], None, 'Unsafe control altered its declared deviation')
                rail_amount += order['notional']+(order['quantity']-order['filled'])*order['limit_price_cents']
                accepted[lifecycle] = record
            same(rail['head'], previous, 'Downstream retained head differs')
            same(rail['E'], rail_amount, 'Forged downstream aggregate')
            promises = [cap for life, cap in caps.items() if life not in accepted and cap['iat'] <= frame['now'] < cap['exp']]
            if frame['id'] == 'promises':
                same(rail['records'], [], 'Pre-redemption checkpoint already executed')
                check(all(not state['receipts'] for state in states.values()), 'Promise checkpoint has local acceptance')
            else:
                same(len(promises), 0, 'Accepted checkpoint hides authority outside prime journal')
                same(set(accepted), set(caps), 'Accepted checkpoint omits downstream lifecycle')
            promise_amount = sum(cap['reservation']['bound'][BUDGET] for cap in promises)
            oracle = {'E': rail_amount+promise_amount, 'outstanding_promises': len(promises),
                      'promise_obligation': promise_amount,
                      'evidence_domain': 'issuance_joined_with_prime' if promises else 'prime_execution_journal'}
            lives = []
            for name, state in states.items():
                for life in obligation(state['capabilities'], state['receipts'], frame['now'])['lifecycles']:
                    lifecycle = name+':'+life['kappa_id']
                    if lifecycle in accepted:
                        order = accepted[lifecycle]['order']
                        consumed = order['notional']
                        residual = (order['quantity']-order['filled'])*order['limit_price_cents']
                        life = {**life, 'E': consumed+residual, 'consumed': consumed, 'residual': residual,
                                'evidence_domain': 'accepted_prime_execution'}
                    lives.append({**life, 'book_id': name, 'lifecycle': lifecycle})
            oracle['lifecycles'] = lives
            aggregate = {'U': sum(s['U'] for s in states.values()), 'Q': sum(s['Q'] for s in states.values()), 'L': 1000}
            fields = checkpoint_fields(frame, aggregate, oracle, 1000)
            same(frame['L_star'], 1000, 'Displayed true ceiling changed')
            for name, state in states.items():
                local_oracle = obligation(state['capabilities'], state['receipts'], frame['now'])
                local = frame['local_books'][name]
                for field in ('U', 'Q', 'L'):
                    same(local[field], state[field], 'Forged local book '+field)
                same(local['E'], local_oracle['E'], 'Local obligation differs')
                same(local['available'], state['L']-state['U']-state['Q'], 'Local headroom differs')
                same(local['ledger_compliant'], state['U']+state['Q'] <= state['L'], 'Local compliance label differs')
                same(local['coverage'], local_oracle['E'] <= state['U']+state['Q'], 'Local coverage label differs')
            same(frame['local_ledger_compliant'], True, 'Local correctness missing from C10 comparison')
            target = expected['C10'][mode]
            values = {name: [states[name]['U'], states[name]['Q']] for name in ('A', 'B')}
            values.update(E=oracle['E'], L_star=1000)
            target = {k: [v*100 for v in value] if isinstance(value, list) else value*100 for k, value in target.items()}
            same(values, target, 'Observed C10 differs from expected-only schedule')
            comparisons.append({'branch': mode, 'checkpoint': frame['id'], 'expected': target, 'observed': values, 'matches': True})
            observed.append(fields)
        last = branch['checkpoints'][-1]['snapshot']
        for field in ('books', 'rail', 'covering'):
            same(branch[field], last[field], 'Final C10 export differs from checkpoint')
        tapes = {name: ordinary(book) for name, book in branch['books'].items()}
        same(branch['ordinary_tapes'], tapes, 'C10 local ordinary tape projection differs')
        for name, admission in branch['admissions'].items():
            if 'kappa' in admission:
                original = index(branch['books'][name]['runs'], 'id')[admission['id']]
                for field in ('proposal', 'request_id', 'decision', 'kappa'):
                    same(admission[field], original[field], 'C10 admission projection differs: '+field)
                same(admission['status'], 'authorized', 'C10 admitted authority mislabeled')
                if mode != 'unsafe':
                    hold = index(branch['covering']['holds'], 'id')[admission['covering_hold']['id']]
                    same(admission['covering_hold'], hold, 'C10 issuance cover projection differs')
            else:
                check(mode == 'covering' and name == 'B', 'Unexpected C10 denied admission')
                same(admission['decision']['code'], 'SHARED_HEADROOM', 'Common-cover denial has wrong cause')
                hold = index(branch['covering']['holds'], 'id')[admission['covering_hold']['id']]
                same(admission['covering_hold'], hold, 'Denied cover projection differs')
                same(hold['status'], 'denied', 'Denied request held usable cover')
                same(admission['request_id'], hold['request_id'], 'Denied request identity differs')
        check(all(t['valid'] for t in tapes.values()), 'C10 local books failed ordinary ledger replay')
        valid = all(c['coverage'] and c['ledger_compliant'] and c['shared_ceiling_compliant'] for c in observed)
        same(branch['ordinary_conformance'], valid, 'C10 aggregate conformance label differs')
        same(branch['historical_violation'], not valid, 'Historical shared breach erased')
        same(branch['intended_breach_detected'], mode == 'unsafe', 'C10 intended breach label differs')
        conformance[mode] = {'valid': valid, 'ordinary_local_tapes': tapes,
                             'shared_scope': {'L_star': 1000, 'valid': valid}}
    return conformance, comparisons


def replay_independent_rail(rail, runtime):
    """B-only signed accepted effects, bound back to A issuance and claims."""
    attest(rail, 'socket-1')
    chain(rail['events'], rail['head'])
    same(rail['keys'], runtime['keys'], 'A/B public trust configuration differs')
    caps = {e['data']['reservation']['id']: e['data'] for e in runtime['events'] if e['kind'] == 'CAPABILITY_ISSUED'}
    claims = {e['data']['execution_id']: e['data'] for e in runtime['events']
              if e['kind'] in ('REMOTE_REDEMPTION_CLAIMED', 'REMOTE_NON_USE_CLAIMED')}
    receipts, executions, orders, nonces = {}, {}, {}, {}
    for event in rail['events']:
        same(event['run_id'], None, 'B imports an in-process Runtime run')
        kind, data = event['kind'], event['data']
        check(kind in ('REMOTE_EXECUTION_ACCEPTED', 'REMOTE_NON_USE_CLOSED', 'REMOTE_FILL_ACCEPTED'), 'Unknown B rail event')
        kappa, rho, execution = data['kappa'], data['receipt'], data['execution']
        same(kappa, caps[kappa['reservation']['id']], 'B capability absent from A issuance')
        verify_artifact(kappa, runtime['keys']['riskbook-1'], 'riskbook-1', 'saac-kappa')
        verify_artifact(rho, rail['keys']['socket-1'], 'socket-1', 'saac-receipt')
        for field in ('snapshot_hash', 'kappa_id', 'nonce', 'aud'):
            same(rho[field], kappa[field], 'Changed B signed lifecycle binding')
        same(rho['reservation_id'], kappa['reservation']['id'], 'Changed B reservation')
        same(execution, {'id': rho['execution_id'], 'revision': rho['revision'], 'result': rho['result']}, 'B execution differs from signed evidence')
        same(rho['settlement'], settlement(kappa['reservation']['bound'], rho['result']['consumed'], rho['result']['released']), 'B evidence violates conservation')
        claim = claims[rho['execution_id']]
        same(claim['kappa_id'], kappa['kappa_id'], 'B acceptance lacks matching A claim')
        same(claim['effect_digest'], kappa['effect_digest'], 'B executes another effect')
        check(rho['receipt_id'] not in receipts, 'Replayed B receipt')
        previous = executions.get(execution['id'])
        same(execution['revision'], previous['revision']+1 if previous else 1, 'Missing/replayed B revision')
        if kind in ('REMOTE_EXECUTION_ACCEPTED', 'REMOTE_NON_USE_CLOSED'):
            check(previous is None and kappa['nonce'] not in nonces, 'Duplicate protected B effect')
            same(data['claim']['execution_id'], execution['id'], 'B claim identity differs')
            same(data['claim']['reservation_id'], rho['reservation_id'], 'B claim reservation differs')
            nonces[kappa['nonce']] = {'nonce': kappa['nonce'], 'status': 'closed' if kind == 'REMOTE_NON_USE_CLOSED' else 'consumed',
                                     'execution_id': execution['id']}
        else:
            check(previous is not None, 'Fill without original B acceptance')
        if previous:
            for field in ('consumed', 'released'):
                check(rho['result'][field][BUDGET] >= previous['result'][field][BUDGET], 'B cumulative outcome regressed')
        order = rho['result'].get('order')
        if order:
            same(order['execution_id'], execution['id'], 'B order lifecycle differs')
            same(order['quantity']*order['limit_price_cents'], kappa['reservation']['bound'][BUDGET], 'B order exceeds authority')
            same(order['notional'], order['filled']*order['limit_price_cents'], 'B fill notional differs')
            same(rho['result']['consumed'][BUDGET], order['notional'], 'B consumed amount differs')
            if order['id'] in orders:
                check(order['version'] > orders[order['id']]['version'], 'B order version did not advance')
            orders[order['id']] = order
        receipts[rho['receipt_id']] = rho
        executions[execution['id']] = execution
    same(index(rail['receipts'], 'receipt_id'), receipts, 'Forged B receipt snapshot')
    same(index(rail['executions'], 'id'), executions, 'Forged B execution snapshot')
    same(index(rail['orders'], 'id'), orders, 'Forged B order snapshot')
    same(index(rail['nonces'], 'nonce'), nonces, 'Forged B redemption snapshot')
    for field in ('runs', 'reservations', 'packs'):
        same(rail[field], [], 'Rail contains competing Runtime state')
    same(rail['allocations'], {}, 'Rail holds competing authoritative budget')
    return receipts


def _verify_c8(bundle, expected):
    same(set(bundle['branches']), {'safe', 'unsafe'}, 'Wrong C8 branches')
    for field in ('shared_database', 'shared_writer', 'shared_transaction', 'shared_authority_object'):
        same(bundle['topology'][field], False, 'C8 process/store independence differs')
    ids = ['promises_issued', 'accepted_receipts_withheld', 'runtime_restarted', 'unsupported_release', 'second_batch', 'accepted_outcome_recovery']
    targets = ['promises', 'accepted', 'restarted', 'unsupported_release', 'second_batch']
    conformance, comparisons = {}, []
    for name, branch in bundle['branches'].items():
        checkpoints = branch['checkpoints']
        same([c['id'] for c in checkpoints], ids, 'Missing C8 checkpoint')
        prior_a, prior_b, observed = [], [], []
        for number, frame in enumerate(checkpoints):
            runtime, rail = frame['snapshot']['runtime'], frame['snapshot']['rail']
            same(frame['snapshot_digest'], digest(frame['snapshot']), 'C8 snapshot digest differs')
            same(runtime['events'][:len(prior_a)], prior_a, 'A history changed across restart/checkpoints')
            same(rail['events'][:len(prior_b)], prior_b, 'B history changed across restart/checkpoints')
            prior_a, prior_b = runtime['events'], rail['events']
            signed = replay_independent_rail(rail, runtime)
            state = replay_book(runtime, name == 'unsafe', signed)
            oracle = obligation(state['capabilities'], signed, frame['now'])
            oracle['evidence_domain'] = 'A issuance/claims joined to B journal' if oracle['outstanding_promises'] else 'B accepted execution/non-use journal'
            lives = {}
            for event in rail['events']:
                data = event['data']
                kappa, execution = data['kappa'], data['execution']
                result = execution['result']
                order = result.get('order')
                consumed = result['consumed'][BUDGET]
                residual = (order['quantity']-order['filled'])*order['limit_price_cents'] if order and order['status'] in ('working', 'partial') else 0
                lives[kappa['kappa_id']] = {'kappa_id': kappa['kappa_id'], 'execution_id': execution['id'],
                    'consumed': consumed, 'residual': residual, 'promise': 0, 'obligation': consumed+residual,
                    'source': 'B', 'event_seq': event['seq'], 'event_hash': event['hash']}
            for kappa in state['capabilities'].values():
                if kappa['kappa_id'] not in lives and kappa['iat'] <= frame['now'] < kappa['exp']:
                    bound = kappa['reservation']['bound'][BUDGET]
                    lives[kappa['kappa_id']] = {'kappa_id': kappa['kappa_id'], 'consumed': 0, 'residual': 0,
                                               'promise': bound, 'obligation': bound, 'source': 'A issuance'}
            same(frame['oracle']['lifecycles'], list(lives.values()), 'C8 oracle lifecycle projection differs')
            same(frame['lifecycles'], list(lives.values()), 'C8 displayed lifecycle projection differs')
            same(frame['observed']['lifecycles'], list(lives.values()), 'C8 observed lifecycle projection differs')
            same(frame['unresolved_dispatch_obligation'], 0, 'Required C8 checkpoint has unresolved dispatch outside B')
            # The C8 observer names its non-authority declaration explicitly.
            for field in ('E', 'outstanding_promises', 'promise_obligation', 'evidence_domain'):
                same(frame['oracle'][field], oracle[field], 'C8 oracle differs: '+field)
            same(frame['oracle']['oracle_is_accepted_institutional_evidence'], False, 'C8 oracle became authority')
            fields = checkpoint_fields({k: v for k, v in frame.items() if k != 'oracle'}, state, oracle)
            for field, value in fields.items():
                same(frame['observed'][field], value, 'C8 observed alias differs')
            same(frame['modeled_available'], 1000-oracle['E'], 'C8 negative modeled headroom clipped')
            same(frame['outstanding_promises_outside_rail'], oracle['outstanding_promises'] > 0, 'B-only evidence claim ignores promises')
            if number < 5:
                same(runtime['receipts'], [], 'Hidden receipts reached A before evidence-delivery phase')
                values = [fields[k] for k in ('U', 'Q', 'E')]
                target = [v*100 for v in expected['C8'][targets[number]][name]]
                same(values, target, 'Observed C8 differs from expected-only checkpoint')
                comparisons.append({'branch': name, 'checkpoint': frame['id'], 'expected': target, 'observed': values, 'matches': True})
            else:
                same([fields[k] for k in ('U', 'Q', 'E')], [0, 1000, 2000] if name == 'unsafe' else [100, 900, 1000], 'Accepted-outcome recovery differs')
            observed.append(fields)
        same(checkpoints[1]['snapshot']['rail'], checkpoints[2]['snapshot']['rail'], 'B did not survive A restart intact')
        for field in ('runtime', 'rail'):
            same(branch[field], checkpoints[-1]['snapshot'][field], 'Final C8 export differs')
        retry = branch['retry']
        for offset, suffix in ((1, 'before'), (2, 'after')):
            process_a = checkpoints[offset]['snapshot']['runtime']['process']
            process_b = checkpoints[offset]['snapshot']['rail']['process']
            same(process_a, {'pid': retry['runtime_pid_'+suffix], 'role': 'runtime', 'store': 'A'}, 'Runtime PID report differs from process-signed checkpoint')
            same(process_b, {'pid': retry['rail_pid_'+suffix], 'role': 'gateway_rail', 'store': 'B'}, 'Rail PID report differs from process-signed checkpoint')
        check(type(retry['runtime_pid_before']) is int and retry['runtime_pid_before'] != retry['runtime_pid_after'], 'Runtime was not a new OS process')
        same(retry['rail_pid_before'], retry['rail_pid_after'], 'Rail restarted with Runtime')
        check(retry['rail_pid_before'] not in (retry['runtime_pid_before'], retry['runtime_pid_after']), 'Runtime and rail share process')
        check(any(e['event'] == 'SIGKILL' and e['pid'] == retry['runtime_pid_before'] and e['exit_code'] == -9
                  for e in branch['process_history']), 'Actual killed Runtime not recorded')
        for field in ('downtime_replay', 'restart_replay', 'same_execution_id'):
            same(retry[field], True, 'Durable retry recovery failed')
        same(len(checkpoints[2]['snapshot']['rail']['orders']), 10, 'Restart created duplicate effect')
        same(len(branch['rail']['orders']), 20 if name == 'unsafe' else 10, 'Wrong protected effect count')
        admissions = index(branch['admissions'], 'request_id')
        same(set(admissions), {f'{prefix}:{i}' for prefix in ('original', 'new') for i in range(10)}, 'Second batch reused original request identities')
        final_runs = index(branch['runtime']['runs'], 'id')
        for run in admissions.values():
            same(run['proposal'], final_runs[run['id']]['proposal'], 'C8 admission intent changed')
            same(run.get('kappa'), final_runs[run['id']].get('kappa'), 'C8 admission authority changed')
        recovery = branch['recovery']
        same(recovery['accepted'], name == 'safe', 'Recovery disposition differs')
        same(recovery['risk_after'], branch['runtime']['risk'], 'Recovery risk differs')
        check(recovery['receipt'] in branch['runtime']['receipts'], 'Recovery evidence was not delivered')
        if name == 'safe':
            same(recovery['duplicate_applied'], False, 'Duplicate receipt applied twice')
            same(recovery['older_applied'], False, 'Older cumulative receipt applied')
        else:
            check(recovery['rejection'] in ('RECEIPT_REGRESSION', 'RECEIPT_TERMINAL'), 'Unsafe recovery was silently repaired')
        valid = all(c['coverage'] and c['ledger_compliant'] and c['shared_ceiling_compliant'] for c in observed)
        same(branch['ordinary_conformance'], valid, 'C8 ordinary conformance label differs')
        same(branch['historical_violation'], not valid, 'C8 historical breach erased')
        same(branch['intended_breach_detected'], name == 'unsafe', 'C8 intended breach label differs')
        normal = ordinary(branch['runtime'])
        check(normal['valid'] == (name == 'safe'), 'C8 normal tape failed to distinguish unsupported release')
        conformance[name] = {'valid': valid, 'ordinary_tape': normal}
    return conformance, comparisons


def verify_bundle(bundle):
    try:
        check(isinstance(bundle, dict), 'Evidence must be an object')
        same(bundle['schema_version'], 1, 'Unknown coverage evidence version')
        check(bundle['case'] in ('C8', 'C9', 'C10'), 'Unknown schedule')
        same(bundle['units']['scale'], 100, 'Domain display scale differs')
        same(bundle['units']['domain'], BUDGET, 'Wrong accounting dimension')
        check(bool(bundle['units']['accounting_window']), 'Missing accounting window')
        expected = json.loads(EXPECTED.read_text())
        if bundle['case'] == 'C9':
            conformance, comparisons = _verify_c9(bundle, expected)
        elif bundle['case'] == 'C8':
            conformance, comparisons = _verify_c8(bundle, expected)
        else:
            conformance, comparisons = _verify_c10(bundle, expected)
        safe = [v['valid'] for k, v in conformance.items() if k != 'unsafe']
        check(all(safe) and not conformance['unsafe']['valid'], 'Safe/negative-control conformance did not separate')
        return {'valid': True, 'negative_control_detected': True, 'conformance': conformance,
                'expected_versus_observed': comparisons, 'errors': [],
                'trust_boundary': 'Local signed fixture history; complete replacement of unanchored keys/history is outside guarantees.'}
    except (EvidenceError, SAACError, KeyError, TypeError, ValueError, IndexError, OSError) as error:
        return {'valid': False, 'negative_control_detected': False, 'conformance': {},
                'expected_versus_observed': [], 'errors': [{'code': getattr(error, 'code', 'EVIDENCE_INVALID'),
                                                         'message': str(error)}]}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    try:
        result = verify_bundle(json.loads(args.bundle.read_text()))
    except (OSError, ValueError) as error:
        result = {'valid': False, 'negative_control_detected': False, 'errors': [{'message': str(error)}]}
    print(json.dumps(result, indent=2))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
