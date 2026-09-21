"""Observed C8 schedule; expected checkpoints remain separate assertions."""
from pathlib import Path
import tempfile

from .crypto import digest
from .remote_rail import BUDGET, EPOCH, UNIT, RemoteRailLab, proposal
from .risk_book import encode


def reconstruct_c8(runtime, rail):
    """Read-only fixture oracle joining issuance to B by lifecycle identity.

    Reservations are deliberately not an obligation source. Even a reservation
    falsely labelled released cannot erase a B effect or a usable promise.
    """
    issued = {e['data']['kappa_id']: e['data'] for e in runtime['events'] if e['kind'] == 'CAPABILITY_ISSUED'}
    claimed = {e['data']['kappa_id'] for e in runtime['events'] if e['kind'] == 'REMOTE_REDEMPTION_CLAIMED'}
    now = runtime.get('now', EPOCH)
    lifecycles = {}
    for event in rail['events']:
        if event['kind'] not in ('REMOTE_EXECUTION_ACCEPTED', 'REMOTE_NON_USE_CLOSED', 'REMOTE_FILL_ACCEPTED'):
            continue
        data = event['data']
        kappa, execution = data['kappa'], data['execution']
        result = execution['result']
        order = result.get('order')
        consumed = result['consumed'][BUDGET]
        residual = ((order['quantity'] - order['filled']) * order['limit_price_cents']
                    if order is not None and order['status'] in ('working', 'partial') else 0)
        lifecycles[kappa['kappa_id']] = {'kappa_id': kappa['kappa_id'], 'execution_id': execution['id'],
            'consumed': consumed, 'residual': residual, 'promise': 0, 'obligation': consumed+residual,
            'source': 'B', 'event_seq': event['seq'], 'event_hash': event['hash']}
    promises, promise_count, dispatches = 0, 0, 0
    for kid, kappa in issued.items():
        if kid not in lifecycles and (kid in claimed or kappa['iat'] <= now < kappa['exp']):
            value = kappa['reservation']['bound'][BUDGET]
            if kid in claimed:
                dispatches += value
            else:
                promises += value
                promise_count += 1
            lifecycles[kid] = {'kappa_id': kid, 'consumed': 0, 'residual': value if kid in claimed else 0,
                              'promise': 0 if kid in claimed else value, 'obligation': value,
                              'source': 'A unresolved dispatch claim' if kid in claimed else 'A issuance'}
    obligation = sum(row['obligation'] for row in lifecycles.values())
    return {'E': obligation, 'outstanding_promises': promise_count, 'promise_obligation': promises,
            'outstanding_promises_outside_rail': bool(promises),
            'unresolved_dispatch_obligation': dispatches,
            'evidence_domain': 'A issuance/claims joined to B journal' if promises or dispatches else 'B accepted execution/non-use journal',
            'lifecycles': list(lifecycles.values()), 'oracle_is_accepted_institutional_evidence': False}


def checkpoint(lab, name, label):
    runtime, rail = lab.runtime('snapshot'), lab.rail('snapshot')
    risk = runtime['risk']['budgets'][BUDGET]
    oracle = reconstruct_c8(runtime, rail)
    observed = {'L': risk['limit'], 'U': risk['used'], 'Q': risk['reserved'], 'available': risk['available'],
                **oracle, 'ledger_compliant': risk['used'] + risk['reserved'] <= risk['limit'],
                'coverage': oracle['E'] <= risk['used'] + risk['reserved'],
                'shared_ceiling_compliant': oracle['E'] <= risk['limit'],
                'coverage_gap': oracle['E'] - risk['used'] - risk['reserved'],
                'modeled_available': risk['limit'] - oracle['E']}
    return {'id': name, 'name': name, 'label': label, 'now': EPOCH, **observed, 'observed': observed, 'oracle': oracle,
            'snapshot': {'runtime': runtime, 'rail': rail},
            'snapshot_digest': digest({'runtime': runtime, 'rail': rail})}


def _branch(directory, unsafe):
    with RemoteRailLab(directory, unsafe=unsafe) as lab:
        stages, admissions = [], []
        original = [lab.runtime('authorize', proposal=proposal(), request_id=f'original:{n}') for n in range(10)]
        assert all('kappa' in run for run in original)
        admissions.extend(original)
        stages.append(checkpoint(lab, 'promises_issued', 'Ten promises issued; no accepted execution in B'))
        accepted = [lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal']) for run in original]
        assert all(attempt['accepted'] and not attempt['replay'] for attempt in accepted)
        stages.append(checkpoint(lab, 'accepted_receipts_withheld', 'Original effects accepted; receipts withheld'))
        old_runtime_pid, rail_pid = lab.runtime_process.pid, lab.rail_process.pid
        before_b = lab.rail('snapshot')
        lab.kill_runtime()
        # B survives while A is absent; retry returns its original durable result.
        downtime_retry = lab.rail('redeem', kappa=original[0]['kappa'], proposal=original[0]['proposal'])
        assert downtime_retry['replay'] and downtime_retry['execution'] == accepted[0]['execution']
        assert lab.rail('snapshot') == before_b
        lab.restart_runtime()
        assert lab.runtime_process.pid != old_runtime_pid and lab.rail_process.pid == rail_pid
        retried = lab.runtime('authorize', proposal=original[0]['proposal'], request_id=original[0]['request_id'])
        assert retried['kappa'] == original[0]['kappa'] and retried['reservation_id'] == original[0]['reservation_id']
        restarted_retry = lab.rail('redeem', kappa=retried['kappa'], proposal=retried['proposal'])
        assert restarted_retry['replay'] and restarted_retry['execution'] == accepted[0]['execution']
        assert len(lab.rail('snapshot')['orders']) == 10
        stages.append(checkpoint(lab, 'runtime_restarted', 'Runtime process restarted; B survives unchanged'))
        if unsafe:
            lab.runtime('unsafe_timeout_release')
        stages.append(checkpoint(lab, 'unsupported_release', 'Unsupported timeout release in unsafe control'))
        second = [lab.runtime('authorize', proposal=proposal(), request_id=f'new:{n}') for n in range(10)]
        admissions.extend(second)
        second_attempts = [lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal']) for run in second if 'kappa' in run]
        assert sum('kappa' in run for run in second) == (10 if unsafe else 0)
        assert all(attempt['accepted'] for attempt in second_attempts)
        stages.append(checkpoint(lab, 'second_batch', 'Second batch denied / durably accepted'))
        # Recovery is explicit, after all required checkpoints. B can progress
        # while A still sees no receipt; the oracle never calls the inbox.
        filled = lab.rail('fill', reservation_id=original[0]['reservation_id'])
        recovery_before = lab.runtime('snapshot')['risk']
        if unsafe:
            from .models import SAACError
            try:
                lab.runtime('evidence', receipt=filled)
            except SAACError as error:
                recovery = {'receipt': filled, 'accepted': False, 'rejection': error.code, 'risk_before': recovery_before,
                            'risk_after': lab.runtime('snapshot')['risk']}
            else:
                raise AssertionError('The ordinary reconciler accepted late settlement after unsupported release')
        else:
            outcome = lab.runtime('evidence', receipt=filled)
            duplicate = lab.runtime('evidence', receipt=filled)
            old = lab.runtime('evidence', receipt=accepted[0]['receipt'])
            assert outcome['applied'] and not duplicate['applied'] and not old['applied']
            recovery = {'receipt': filled, 'accepted': True, 'duplicate_applied': duplicate['applied'],
                        'older_applied': old['applied'], 'risk_before': recovery_before,
                        'risk_after': lab.runtime('snapshot')['risk']}
            assert recovery['risk_after']['budgets'][BUDGET] == {'limit': 1000, 'used': 100, 'reserved': 900, 'available': 0}
        stages.append(checkpoint(lab, 'accepted_outcome_recovery', 'Accepted outcome delivered through normal reconciliation'))
        failed = [s['name'] for s in stages if not s['observed']['coverage'] or not s['observed']['ledger_compliant'] or not s['observed']['shared_ceiling_compliant']]
        return {'name': 'Unsafe timeout-release control' if unsafe else 'Evidence-preserving Runtime',
                'unsafe': unsafe, 'checkpoints': stages, 'admissions': admissions,
                'runtime': lab.runtime('snapshot'), 'rail': lab.rail('snapshot'), 'process_history': lab.process_history,
                'retry': {'runtime_pid_before': old_runtime_pid, 'runtime_pid_after': lab.runtime_process.pid,
                          'rail_pid_before': rail_pid, 'rail_pid_after': lab.rail_process.pid,
                          'downtime_replay': downtime_retry['replay'], 'restart_replay': restarted_retry['replay'],
                          'same_execution_id': restarted_retry['execution']['id'] == accepted[0]['execution']['id'],
                          'original_execution_count': len(before_b['orders']), 'second_execution_count': len(second_attempts)},
                'recovery': recovery, 'ordinary_conformance': not failed, 'failed_checkpoints': failed,
                'historical_violation': bool(failed),
                'intended_breach_detected': unsafe and 'unsupported_release' in failed and 'second_batch' in failed}


def run_c8(directory):
    """Run both fixed schedules, export only JSON evidence, delete live stores."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / 'observed.json'
    if output.exists():
        raise FileExistsError('C8 evidence is immutable; choose a new output directory.')
    with tempfile.TemporaryDirectory(prefix='risc-c8-state-') as state:
        branches = {name: _branch(Path(state) / name, name == 'unsafe') for name in ('safe', 'unsafe')}
    result = {'schema_version': 1, 'case': 'C8', 'title': 'Independent rail survives a Runtime restart', 'status': 'implemented/unpinned',
              'units': {'domain': BUDGET, 'scale': UNIT, 'accounting_window': f'fixed synthetic window at {EPOCH}'},
              'clock': {'epoch': EPOCH, 'kind': 'frozen clock with ordered schedule phases'},
              'topology': {'runtime': 'separate OS process; store A', 'gateway_rail': 'separate OS process; store B',
                           'transport': 'private Unix-domain fixed-command JSON; same host', 'shared_database': False,
                           'shared_writer': False, 'shared_transaction': False, 'shared_authority_object': False,
                           'public_or_hosted_support': 'unsupported; local trusted operator only'},
              'branches': branches,
              'limitations': ['Synthetic order acceptance and fills, not a live OMS.',
                  'Same-host process separation is not host compromise resistance or distributed exactly-once execution.',
                  'A claim without accepted B evidence retains allocation; no availability claim is made.',
                  'Private sockets are trusted operator interfaces; actors have no access. Same-user hostile code is outside this isolation.',
                  'A replaced, unanchored fixture history is outside the verifier guarantee.']}
    with output.open('x') as handle:
        handle.write(encode(result) + '\n')
    return result
