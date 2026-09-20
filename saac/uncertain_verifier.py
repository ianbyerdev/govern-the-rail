"""Offline verification of the bounded uncertainty experiment's evidence.

This module deliberately does not import the experiment's observer or dashboard
projection. It rebuilds both from the exported chronological records. Public
keys, the local event history and the synthetic rail remain fixture trust roots;
there is no external checkpoint or external-world attestation.
"""
from copy import deepcopy

from .accounting import settlement
from .crypto import digest
from .packs import evaluate
from .protocol import verify_artifact
from .tape import verify_tape


STAGES = ('admission', 'hidden_fills', 'deadline', 'second_batch', 'recovered_fills', 'cancellations')
OFFSETS = (0, 1, 61, 62, 63, 64)
BUDGET = 'notional_usd_cents'
ORACLE_FIELDS = ('executed_units', 'working_units', 'obligation_units', 'outstanding_promises',
                 'total_commitment_units', 'breach_units', 'modeled_available_units')


class EvidenceError(ValueError):
    pass


def check(condition, message):
    if not condition:
        raise EvidenceError(message)


def same(actual, expected, message):
    # Canonical equality also rejects bool-for-integer and float substitutions.
    if isinstance(actual, set):
        actual = sorted(actual)
    if isinstance(expected, set):
        expected = sorted(expected)
    check(digest(actual) == digest(expected), message)


def indexed(values, key, label):
    result = {value[key]: value for value in values}
    check(len(result) == len(values), f'{label}: duplicate {key}')
    return result


def _reconstruct(book, policy, events, now, config):
    unit, limit = config['unit_notional_cents'], config['limit_units']
    reservations, capabilities, proposals, orders, nonces, receipts, executions = {}, {}, {}, {}, {}, {}, {}
    states, rejections, records, issued_by_run, history, execution_events = {}, [], [], {}, [], []
    accepted, investigations, invalid = 0, 0, 0
    previous = 'genesis'
    runs = indexed(book['runs'], 'id', 'runs')

    def totals():
        used = sum(r['consumed'] for r in reservations.values())
        reserved = sum(r['bound'] - r['consumed'] - r['released'] for r in reservations.values())
        return {'used': used, 'reserved': reserved, 'limit': limit * unit,
                'available': limit * unit - used - reserved}

    def risk_matches(risk, context):
        same(risk, {'budgets': {BUDGET: totals()}}, context)

    for seq, event in enumerate(events, 1):
        same(event['seq'], seq, 'Event sequence is not contiguous')
        same(event['prev_hash'], previous, 'Event hash predecessor differs')
        same(event['hash'], digest({k: v for k, v in event.items() if k != 'hash'}), 'Event hash differs')
        previous = event['hash']
        check(type(event['ts']) is int and config['frozen_epoch'] <= event['ts'] <= now,
              'Event timestamp lies outside the recorded virtual interval')
        kind, data, run_id = event['kind'], event['data'], event['run_id']
        if kind == 'PROPOSAL_CREATED':
            check(run_id not in proposals, 'A proposal was created twice')
            proposals[run_id] = data
            same(runs[run_id]['proposal'], data, 'Exported proposal differs from its creation event')
            actor = int(data['agent_id'].rsplit('.', 1)[1])
            same(data['grant_id'], f'G-UNCERTAIN-{actor:03}', 'Actor/grant identity differs')
            if data['operation'] == 'order.submit':
                check(0 <= actor < 110, 'Unexpected fixture actor')
                request_id = f'original:{actor:03}' if actor < 100 else f'new:{actor - 100:03}'
                same(data['quantity'], 1, 'Each admission must request one share')
                same(data['limit_price_cents'], unit, 'Each admission must request one displayed unit')
            else:
                same(data['operation'], 'order.cancel', 'Unexpected experiment operation')
                check(0 <= actor < 100, 'Cancellation must belong to an original actor')
                request_id = f'cancel:{actor:03}'
            same(runs[run_id]['request_id'], request_id, 'Request identity does not bind its distinct actor/batch')
        elif kind == 'POLICY_EVALUATED':
            same(evaluate(**data['inputs']), data['decision'], 'Atomic policy verdict does not replay')
            pack = data['inputs']['pack']
            verify_artifact(pack, book['keys']['riskbook-1'], 'riskbook-1', 'saac-pack')
            same(pack['limits'], {BUDGET: limit * unit}, 'Policy ceiling differs from the matched fixture')
            same(pack['per_action'], {BUDGET: unit}, 'Policy action bound differs between books')
            risk_matches(data['inputs']['risk'], 'Policy used counters other than reconstructed authority')
        elif kind == 'RISK_RESERVED':
            risk_matches(data['risk_before'], 'Reservation does not begin at reconstructed authority counters')
            check(data['id'] not in reservations, 'Reservation identity reused')
            same(set(data['bound']), {BUDGET}, 'Wrong budget dimension')
            bound = data['bound'][BUDGET]
            check(type(bound) is int and bound in (0, unit), 'Invalid reservation bound')
            check(bound <= totals()['available'], 'Admission exceeded available capacity')
            reservations[data['id']] = {'id': data['id'], 'run_id': run_id, 'bound': bound,
                'consumed': 0, 'released': 0, 'revision': 0, 'status': 'reserved',
                'receipt_hash': None, 'book_seq': seq}
        elif kind == 'CAPABILITY_ISSUED':
            verify_artifact(data, book['keys']['riskbook-1'], 'riskbook-1', 'saac-kappa')
            rid = data['reservation']['id']
            row = reservations[rid]
            check(rid not in capabilities and run_id not in issued_by_run, 'Capability issued twice')
            same(row['run_id'], run_id, 'Capability belongs to a different reservation proposal')
            same(data['reservation']['bound'], {BUDGET: row['bound']}, 'Capability reservation bound differs')
            same(data['reservation']['book_seq'], row['book_seq'], 'Capability precedes reservation commit event')
            same(data['effect_digest'], digest(data['effect']), 'Capability effect digest differs')
            same(data['snapshot_hash'], digest(runs[run_id]['snapshot']), 'Capability snapshot binding differs')
            same(data, runs[run_id]['kappa'], 'Exported capability differs from issuance')
            same(data['agent_id'], proposals[run_id]['agent_id'], 'Capability actor differs')
            same(data['grant_id'], proposals[run_id]['grant_id'], 'Capability grant differs')
            same(data['effect']['o'], proposals[run_id]['operation'], 'Capability operation differs')
            check(data['nonce'] not in nonces, 'Nonce issued twice')
            capabilities[rid] = data
            issued_by_run[run_id] = data
            nonces[data['nonce']] = {'nonce': data['nonce'], 'status': 'unused', 'execution_id': None}
        elif kind == 'EXECUTION_STARTED':
            kappa = issued_by_run[run_id]
            nonce = nonces[kappa['nonce']]
            same(nonce['status'], 'unused', 'Single-use authority executed twice')
            check(kappa['iat'] <= event['ts'] < kappa['exp'], 'Expired capability executed')
            same(data['effect'], kappa['effect'], 'Execution did not bind the exact authorized effect')
            nonce.update(status='consumed', execution_id=data['execution_id'])
            reservations[kappa['reservation']['id']]['status'] = 'executing'
        elif kind == 'RECEIPT_CREATED':
            verify_artifact(data, book['keys']['socket-1'], 'socket-1', 'saac-receipt')
            kappa = capabilities[data['reservation_id']]
            same(run_id, reservations[data['reservation_id']]['run_id'], 'Receipt event proposal binding differs')
            for rho_key, kappa_key in (('snapshot_hash', 'snapshot_hash'), ('nonce', 'nonce'),
                                        ('kappa_id', 'kappa_id'), ('aud', 'aud')):
                same(data[rho_key], kappa[kappa_key], 'Receipt capability binding differs')
            check(data['receipt_id'] not in receipts, 'Receipt identity reused')
            same(data['settlement'], settlement(kappa['reservation']['bound'], data['result']['consumed'],
                                                data['result']['released']), 'Receipt violates conservation')
            nonce = nonces[kappa['nonce']]
            if data['result']['status'] == 'non_use':
                same(nonce['status'], 'unused', 'Non-use closure raced after redemption')
                nonce.update(status='closed', execution_id=data['execution_id'])
            same(nonce['execution_id'], data['execution_id'], 'Receipt lacks matching durable redemption/closure')
            prior = executions.get(data['execution_id'])
            same(data['revision'], prior['revision'] + 1 if prior else 1, 'Receipt revisions are not cumulative')
            receipts[data['receipt_id']] = data
            executions[data['execution_id']] = {'id': data['execution_id'], 'revision': data['revision'], 'result': data['result']}
        elif kind == 'RECEIPT_RECONCILED':
            rho = data['receipt']
            same(receipts[rho['receipt_id']], rho, 'Accepted receipt was not present in the signed journal')
            row = reservations[rho['reservation_id']]
            same(run_id, row['run_id'], 'Reconciliation event proposal binding differs')
            risk_matches(data['risk_before'], 'Receipt risk-before differs')
            check(rho['revision'] > row['revision'], 'Accepted revision did not advance')
            check(row['status'] not in ('released', 'settled'), 'Terminal reservation reopened')
            consumed, released = rho['settlement']['consumed'][BUDGET], rho['settlement']['released'][BUDGET]
            check(consumed >= row['consumed'] and released >= row['released'], 'Accepted evidence regressed')
            row.update(consumed=consumed, released=released, revision=rho['revision'], receipt_hash=digest(rho),
                       status=rho['settlement']['reservation_status'])
            risk_matches(data['risk_after'], 'Receipt risk-after differs')
            accepted += 1
        elif kind == 'RECEIPT_DUPLICATE':
            rho = receipts[data['receipt_id']]
            row = reservations[rho['reservation_id']]
            same(run_id, row['run_id'], 'Duplicate evidence proposal binding differs')
            same(data['revision'], rho['revision'], 'Duplicate evidence revision differs')
            check(rho['revision'] <= row['revision'], 'Unaccepted evidence was labeled duplicate')
        elif kind == 'RECONCILIATION_REQUIRED':
            same(run_id, reservations[data['id']]['run_id'], 'Uncertain reservation proposal binding differs')
            reservations[data['id']]['status'] = 'uncertain'
        elif kind == 'EXPERIMENT_INVALID_TIMEOUT_RELEASE':
            same(policy, 'timeout', 'Incorrect timeout policy entered the evidence-based book')
            same(data['evidence_class'], 'deliberately_invalid_experimental_accounting_transition', 'Unsafe transition is not explicitly labeled')
            same(data['terminal_evidence'], None, 'Timeout release fabricated terminal evidence')
            same(data['normal_reconciler_bypassed_for_this_transition_only'], True, 'Unsafe deviation is not declared')
            check(event['ts'] >= config['reconcile_by'], 'Timeout release preceded its fixed deadline')
            risk_matches(data['risk_before'], 'Invalid transition risk-before differs')
            row = reservations[data['reservation_id']]
            same(run_id, row['run_id'], 'Timeout release proposal binding differs')
            check(row['released'] == 0 and row['consumed'] == 0, 'Timeout transition repeated or altered consumption')
            row.update(released=row['bound'], status='released')
            risk_matches(data['risk_after'], 'Invalid transition risk-after differs')
            invalid += 1
        elif kind == 'EXPERIMENT_RECONCILIATION_STATE':
            check(data['reservation_id'] in reservations, 'Exception references unknown reservation')
            check(data['state'] in ('due', 'in_progress', 'unresolved', 'resolved'), 'Unknown reconciliation state')
            same(data['due_at'], config['reconcile_by'], 'Actor-controlled or mismatched reconciliation deadline')
            states[data['reservation_id']] = data
            history.append({'ts': event['ts'], **data})
        elif kind == 'EXPERIMENT_EVIDENCE_QUERIED':
            investigations += 1
        elif kind == 'EXPERIMENT_LATE_RECEIPT_REJECTED':
            same(policy, 'timeout', 'Correct book rejected required recovery evidence')
            same(data['receipt'], receipts[data['receipt']['receipt_id']], 'Rejected receipt differs from signed journal')
            check(data['code'] in ('RECEIPT_REGRESSION', 'RECEIPT_TERMINAL'), 'Unexpected late-evidence rejection')
            rejections.append(data)
        elif kind == 'EXPERIMENT_CONFIGURED':
            same(data['policy'], policy, 'Recorded policy label differs')
            same(data['config'], config, 'Recorded configuration differs')
        order = data.get('result', {}).get('order') if kind == 'EXECUTION_COMPLETED' else data if kind == 'EMS_FILL' else None
        if order is not None:
            if kind == 'EXECUTION_COMPLETED':
                execution_events.append(data)
            check(order['status'] in ('working', 'partial', 'filled', 'cancelled'), 'Unknown rail order status')
            check(type(order['filled']) is int and 0 <= order['filled'] <= order['quantity'], 'Invalid fill quantity')
            same(order['quantity'], 1, 'Observer order quantity changed')
            same(order['limit_price_cents'], unit, 'Observer order price changed')
            same(order['notional'], order['filled'] * unit, 'Observer executed notional differs from actual fills')
            if order['id'] in orders:
                prior = orders[order['id']]
                check(order['version'] > prior['version'], 'Durable order revision did not advance')
                same(order['execution_id'], prior['execution_id'], 'Order execution identity changed')
                check(order['filled'] >= prior['filled'], 'Durable fills were erased')
            orders[order['id']] = order
            records.append({'source_event_seq': seq, 'source_event_hash': event['hash'], 'ts': event['ts'],
                            'kind': kind, 'order': order, 'evidence_class': 'experimental_fixture_oracle_observation'})

    # A self-consistent rehashed oracle is insufficient: every reported order
    # transition must also exist in genuine signed socket results. The oracle
    # remains a separate projection; these comparisons never settle the book.
    receipt_revisions = {(rho['execution_id'], rho['revision']): rho for rho in receipts.values()}
    for execution in execution_events:
        rho = receipt_revisions[(execution['id'], execution['revision'])]
        same(execution['result'], rho['result'], 'Unsigned execution event differs from its signed result')
    signed_orders = [rho['result']['order'] for rho in receipts.values() if 'order' in rho['result']]
    for record in records:
        check(any(digest(record['order']) == digest(order) for order in signed_orders),
              'Oracle order event lacks matching signed institutional evidence')
    for order in orders.values():
        same(order, executions[order['execution_id']]['result']['order'],
             'Latest oracle order differs from latest signed original execution result')

    values = totals()
    check(all(value % unit == 0 for value in values.values()), 'Displayed units truncate fractional commitments')
    displayed = {'limit': limit, 'consumed': values['used'] // unit,
                 'reserved': values['reserved'] // unit, 'available': values['available'] // unit}
    executed = sum(order['notional'] for order in orders.values())
    working = sum((order['quantity'] - order['filled']) * order['limit_price_cents']
                  for order in orders.values() if order['status'] in ('working', 'partial'))
    promises = sum(k['reservation']['bound'][BUDGET] for k in capabilities.values()
                   if k['effect']['o'] == 'order.submit' and nonces[k['nonce']]['status'] == 'unused'
                   and k['iat'] <= now < k['exp'])
    total = (executed + working + promises) // unit
    observer = {'executed_units': executed // unit, 'working_units': working // unit,
                'obligation_units': (executed + working) // unit, 'outstanding_promises': promises // unit,
                'total_commitment_units': total, 'breach_units': max(0, total - limit),
                'modeled_available_units': limit - total}
    admissions = {}
    for batch, label in (('original:', 'original'), ('new:', 'new')):
        batch_runs = [run_id for run_id in proposals if runs[run_id]['request_id'].startswith(batch)]
        admitted = sum(run_id in issued_by_run for run_id in batch_runs)
        admissions[label] = {'requested': len(batch_runs), 'admitted': admitted, 'denied': len(batch_runs) - admitted}
    rejected_receipts = {rejection['receipt']['receipt_id'] for rejection in rejections}
    withheld = {rho['reservation_id'] for rho in receipts.values()
                if rho['revision'] > reservations[rho['reservation_id']]['revision'] and rho['receipt_id'] not in rejected_receipts}
    counts = {state: sum(item['state'] == state for item in states.values())
              for state in ('due', 'in_progress', 'unresolved', 'resolved')}
    state = next((state for state in ('unresolved', 'in_progress', 'due', 'resolved') if counts[state]), 'waiting')
    panel = {'book': displayed, 'admission': admissions,
             'rail': {'working_orders': sum(o['status'] in ('working', 'partial') for o in orders.values()),
                      'filled_orders': sum(o['status'] == 'filled' for o in orders.values()),
                      'cancelled_orders': sum(o['status'] == 'cancelled' for o in orders.values()),
                      'executed_units': executed // unit, 'withheld_receipts': len(withheld), 'accepted_evidence': accepted},
             'observer': {**observer, 'unaccounted_units': total - displayed['consumed'] - displayed['reserved']},
             'reconciliation': {**counts, 'state': state, 'investigations_started': investigations,
                 'deadlines_reached': sum(item['state'] == 'due' for item in history), 'history': history,
                 'exceptions': [{**item, 'age_seconds': now - item['due_at']} for item in states.values() if item['state'] == 'unresolved']},
             'invalid_transitions': invalid, 'receipt_rejections': rejections,
             'event_seq': len(events), 'event_head': previous}
    return {'panel': panel, 'observer': observer, 'orders': orders, 'records': records,
            'reservations': reservations, 'capabilities': capabilities, 'receipts': receipts,
            'nonces': nonces, 'executions': executions, 'proposals': proposals}


def _compare_panel(actual, expected, context):
    for field in ('book', 'admission', 'rail', 'reconciliation', 'invalid_transitions',
                  'receipt_rejections', 'event_seq', 'event_head'):
        same(actual[field], expected[field], f'{context}: dashboard {field} differs from reconstructed evidence')
    for field in (*ORACLE_FIELDS, 'unaccounted_units'):
        same(actual['observer'][field], expected['observer'][field], f'{context}: observer {field} differs')


def _verify(bundle):
    config, view = bundle['config'], bundle['view']
    same(bundle['schema_version'], 1, 'Unknown evidence schema')
    for key, expected in {'limit_units': 10, 'unit_notional_cents': 100, 'original_actors': 100,
                          'new_requests': 10, 'hidden_fills': 4, 'capability_ttl_seconds': 30,
                          'reconcile_after_seconds': 60, 'frozen_epoch': 1800000000, 'seed': 0}.items():
        same(config[key], expected, f'Configuration {key} differs from the bounded matched experiment')
    same(config['reconcile_by'], config['frozen_epoch'] + 60, 'Deadline differs from recorded frozen clock')
    same(view['config'], config, 'Dashboard configuration differs')
    same(bundle['frames'], view['frames'], 'Exported frames differ from dashboard history')
    frames = bundle['frames']
    check(len(frames) <= len(STAGES), 'Too many frames')
    completed = list(STAGES[:len(frames)])
    same([frame['stage'] for frame in frames], completed, 'Frames are missing, repeated or reordered')
    same(view['completed_stages'], completed, 'Dashboard completion history differs')
    same(view['stage'], completed[-1] if completed else 'ready', 'Dashboard stage differs')
    same(view['now'], config['frozen_epoch'] + (OFFSETS[len(frames) - 1] if frames else 0), 'Dashboard clock differs')
    same([entry['stage'] for entry in bundle['schedule']], list(STAGES), 'Recorded event schedule differs')
    same([entry['virtual_time'] for entry in bundle['schedule']],
         [config['frozen_epoch'] + offset for offset in OFFSETS], 'Recorded virtual-time schedule differs')
    same(set(bundle['books']), {'evidence', 'timeout'}, 'Exactly two independent books are required')
    conformance, projections = {}, {}
    for policy in ('evidence', 'timeout'):
        book = bundle['books'][policy]
        same(set(book['keys']), {'riskbook-1', 'socket-1'}, 'Unexpected exported verification keys')
        events = book['events']
        latest = _reconstruct(book, policy, events, view['now'], config)
        projections[policy] = latest
        _compare_panel(view['policies'][policy], latest['panel'], f'{policy} final')
        same(indexed(book['orders'], 'id', 'orders'), latest['orders'], 'Exported order table differs from rail events')
        same(indexed(book['receipts'], 'receipt_id', 'receipts'), latest['receipts'], 'Exported receipt journal differs from signed events')
        same(indexed(book['nonces'], 'nonce', 'nonces'), latest['nonces'], 'Exported nonce journal differs from redemption/closure events')
        same(indexed(book['executions'], 'id', 'executions'), latest['executions'], 'Exported execution journal differs from signed cumulative results')
        same({run['id'] for run in book['runs']}, set(latest['proposals']), 'Exported run manifest is incomplete')
        request_ids = [run['request_id'] for run in book['runs']]
        check(len(request_ids) == len(set(request_ids)), 'Fresh requests reused an earlier request identifier')
        exported_reservations = indexed(book['reservations'], 'id', 'reservations')
        same(set(exported_reservations), set(latest['reservations']), 'Reservation manifest differs')
        expected_allocations = {}
        for rid, row in latest['reservations'].items():
            for field in ('id', 'run_id', 'bound', 'consumed', 'released', 'revision', 'status', 'receipt_hash'):
                same(exported_reservations[rid][field], row[field], f'Reservation {field} differs from replay')
            same(exported_reservations[rid]['kappa'], latest['capabilities'][rid], 'Reservation signed artifact differs')
            expected_allocations[rid] = {BUDGET: {field: row[field] for field in ('bound', 'consumed', 'released')}}
        same(book['allocations'], expected_allocations, 'Exported allocations differ from reconstructed ledger')
        for field in ORACLE_FIELDS:
            same(book['observer'][field], latest['observer'][field], f'Exported observer {field} differs')
        same(indexed(book['observer']['orders'], 'id', 'observer orders'), latest['orders'], 'Oracle order projection differs')
        same(book['observer']['records'], latest['records'], 'Oracle source records differ from durable rail events')
        try:
            normal = verify_tape(events, book['keys'])
        except Exception as error:
            normal = {'valid': False, 'code': getattr(error, 'code', type(error).__name__), 'message': str(error)}
        conformance[policy] = deepcopy(normal)
        if latest['panel']['invalid_transitions']:
            conformance[policy] = {'valid': False, 'code': 'EXPERIMENT_INVALID_TIMEOUT_RELEASE',
                'message': 'Deliberately invalid release is never certified as normal conformance.', 'ordinary_tape': normal}
        if policy == 'evidence':
            check(normal['valid'], 'Correct-path ordinary conformance failed')
        elif len(frames) >= 4:
            check(not normal['valid'], 'Ordinary verifier failed to detect unsafe repeated allocation')
        previous_seq = 0
        for index, frame in enumerate(frames):
            same(frame['now'], config['frozen_epoch'] + OFFSETS[index], 'Frame clock differs')
            actual = frame['policies'][policy]
            seq = actual['event_seq']
            check(type(seq) is int and previous_seq < seq <= len(events), 'Frame event boundary is invalid')
            previous_seq = seq
            projected = _reconstruct(book, policy, events[:seq], frame['now'], config)['panel']
            _compare_panel(actual, projected, f'{policy}/{frame["stage"]}')
            expected_book = [(0, 10, 0), (0, 10, 0),
                             (0, 10, 0) if policy == 'evidence' else (0, 0, 10),
                             (0, 10, 0), (4, 6, 0) if policy == 'evidence' else (0, 10, 0),
                             (4, 0, 6) if policy == 'evidence' else (0, 10, 0)][index]
            same(projected['book'], dict(zip(('limit', 'consumed', 'reserved', 'available'), (10, *expected_book))),
                 'Observed authority outcome does not establish the scenario claim')
            same(projected['admission']['original'], {'requested': 100, 'admitted': 10, 'denied': 90}, 'Initial admission was not exactly ten of 100')
            expected_new = {'requested': 0, 'admitted': 0, 'denied': 0} if index < 3 else {
                'requested': 10, 'admitted': 0 if policy == 'evidence' else 10, 'denied': 10 if policy == 'evidence' else 0}
            same(projected['admission']['new'], expected_new, 'Second admission does not match the policy comparison')
            obligation = 0 if index == 0 else 4 if index == 5 and policy == 'evidence' else 14 if index == 5 else 20 if index >= 3 and policy == 'timeout' else 10
            same(projected['observer']['obligation_units'], obligation, 'Independent rail obligations do not establish the scenario claim')
            same(projected['observer']['outstanding_promises'], 10 if index == 0 else 0, 'Outstanding promises were omitted or double-counted')
        if frames:
            _compare_panel(view['policies'][policy], frames[-1]['policies'][policy], 'Final dashboard/frame agreement')
    check(bundle['books']['evidence']['keys'] != bundle['books']['timeout']['keys'], 'Comparison books share signing identities')
    detected = len(frames) >= 4 and frames[3]['policies']['timeout']['observer']['breach_units'] == 10
    return {'valid': True, 'negative_control_detected': detected, 'conformance': conformance,
            'completed_stages': completed, 'checks': {
                'hash_chains_and_frame_boundaries': True, 'all_capability_and_receipt_signatures': True,
                'atomic_admission_and_exact_execution_bindings': True, 'nonce_single_use_and_closure': True,
                'independent_authority_reconstruction': True, 'independent_rail_observer_reconstruction': True,
                'all_dashboard_frames_and_final_journals': True, 'expected_completed_stage_outcomes': True},
            'trust': 'The supplied public keys, local history and synthetic rail are fixture trust roots. No external checkpoint, external audit, production broker behavior or distributed deployment is attested.'}


def verify_bundle(bundle):
    """Return a fail-closed report for complete or prefix-stage exports."""
    try:
        return _verify(bundle)
    except Exception as error:
        return {'valid': False, 'negative_control_detected': False,
                'errors': [{'code': getattr(error, 'code', type(error).__name__), 'message': str(error)}],
                'conformance': {'evidence': {'valid': False}, 'timeout': {'valid': False}}, 'checks': {}}


def main():
    import argparse
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Independently verify exported uncertain-execution evidence')
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    try:
        report = verify_bundle(json.loads(args.bundle.read_text()))
    except (OSError, ValueError) as error:
        report = {'valid': False, 'negative_control_detected': False, 'errors': [{'message': str(error)}]}
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['valid'] else 1)


if __name__ == '__main__':
    main()
