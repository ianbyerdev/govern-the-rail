"""C8 exercises actual OS-process death, independent stores and fixed RPC."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest

from saac.coverage_c8 import reconstruct_c8, run_c8
from saac.crypto import digest
from saac.models import SAACError
from saac.protocol import verify_artifact
from saac.remote_rail import BUDGET, RemoteRailLab, proposal


@pytest.fixture(scope='module')
def observed(tmp_path_factory):
    return run_c8(tmp_path_factory.mktemp('c8-observed'))


def test_separate_process_restart_and_exact_checkpoints(observed):
    expected = {
        'safe': [(0, 1000, 1000), (0, 1000, 1000), (0, 1000, 1000), (0, 1000, 1000), (0, 1000, 1000)],
        'unsafe': [(0, 1000, 1000), (0, 1000, 1000), (0, 1000, 1000), (0, 0, 1000), (0, 1000, 2000)],
    }
    for name, branch in observed['branches'].items():
        points = branch['checkpoints'][:5]
        assert [(p['observed']['U'], p['observed']['Q'], p['observed']['E']) for p in points] == expected[name]
        retry = branch['retry']
        assert retry['runtime_pid_before'] != retry['runtime_pid_after']
        assert retry['runtime_pid_before'] != retry['rail_pid_before']
        assert retry['rail_pid_before'] == retry['rail_pid_after']
        assert retry['same_execution_id'] and retry['downtime_replay'] and retry['restart_replay']
        assert any(e['event'] == 'SIGKILL' and e['exit_code'] == -9 for e in branch['process_history'])
        assert retry['original_execution_count'] == 10
        assert retry['second_execution_count'] == (10 if name == 'unsafe' else 0)
        assert all(p['snapshot']['runtime']['receipts'] == [] for p in points)
        assert all(p['observed']['ledger_compliant'] for p in points)
    assert observed['branches']['safe']['ordinary_conformance']
    assert not observed['branches']['unsafe']['ordinary_conformance']
    assert observed['branches']['unsafe']['intended_breach_detected']
    assert observed['branches']['unsafe']['checkpoints'][4]['observed']['modeled_available'] == -1000


def test_promises_require_issuance_history_and_no_double_count(observed):
    for branch in observed['branches'].values():
        before = branch['checkpoints'][0]
        assert not before['snapshot']['rail']['orders']
        assert before['observed']['outstanding_promises'] == 10
        assert before['observed']['promise_obligation'] == 1000
        assert before['observed']['outstanding_promises_outside_rail']
        for point in branch['checkpoints'][1:]:
            assert point['observed']['outstanding_promises'] == 0
            assert not point['observed']['outstanding_promises_outside_rail']
            assert 'B accepted' in point['observed']['evidence_domain']
            reconstruction = reconstruct_c8(**point['snapshot'])
            assert reconstruction['E'] == point['observed']['E']
            assert len({r['kappa_id'] for r in reconstruction['lifecycles']}) == len(reconstruction['lifecycles'])


def test_accepted_evidence_recovery_uses_ordinary_reconciler(observed):
    safe = observed['branches']['safe']
    recovery = safe['recovery']
    assert recovery['accepted'] and not recovery['duplicate_applied'] and not recovery['older_applied']
    assert recovery['risk_before']['budgets'][BUDGET]['used'] == 0
    assert recovery['risk_after']['budgets'][BUDGET] == {'limit': 1000, 'used': 100, 'reserved': 900, 'available': 0}
    kinds = [e['kind'] for e in safe['runtime']['events']]
    assert 'REMOTE_EVIDENCE_DELIVERED' in kinds and 'RECEIPT_RECONCILED' in kinds
    unsafe = observed['branches']['unsafe']['recovery']
    assert not unsafe['accepted'] and unsafe['rejection'] in ('RECEIPT_REGRESSION', 'RECEIPT_TERMINAL')
    assert unsafe['risk_after'] == unsafe['risk_before']


def test_exports_only_public_artifacts_and_signed_receipts(observed, tmp_path):
    for branch in observed['branches'].values():
        for run in branch['runtime']['runs']:
            if 'kappa' in run:
                verify_artifact(run['kappa'], branch['runtime']['keys']['riskbook-1'], 'riskbook-1', 'saac-kappa')
        for receipt in branch['rail']['receipts']:
            verify_artifact(receipt, branch['rail']['keys']['socket-1'], 'socket-1', 'saac-receipt')
        for point in branch['checkpoints']:
            for role, kid in (('runtime', 'riskbook-1'), ('rail', 'socket-1')):
                snapshot = point['snapshot'][role]
                attestation = snapshot['attestation']
                verify_artifact(attestation, snapshot['keys'][kid], kid, 'coverage-book-checkpoint')
                assert attestation['snapshot_digest'] == digest({k: v for k, v in snapshot.items() if k != 'attestation'})
                assert attestation['head'] == snapshot['head']
                assert attestation['evidence_class'] == 'local_fixture_attestation_not_accepted_outcome_authority'
        assert not any('private' in key for key in branch['runtime']['keys'])
    (tmp_path / 'observed.json').write_text('previous evidence')
    with pytest.raises(FileExistsError):
        run_c8(tmp_path)
    assert (tmp_path / 'observed.json').read_text() == 'previous evidence'


def test_missing_eligibility_fails_closed_and_preserves_allocation(tmp_path):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id='missing-runtime')
        lab.kill_runtime()
        with pytest.raises(SAACError) as error:
            lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'])
        assert error.value.code == 'REMOTE_UNAVAILABLE'
        assert lab.rail('snapshot')['orders'] == []
        lab.restart_runtime()
        assert lab.runtime('snapshot')['risk']['budgets'][BUDGET]['reserved'] == 100
        assert lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'])['accepted']


def test_interrupted_remote_claim_cannot_release_and_retry_recovers(tmp_path):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id='interrupted-claim')
        with pytest.raises(SAACError) as error:
            lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'], interrupt_after_claim=True)
        assert error.value.code == 'INTERRUPTED_DISPATCH'
        assert not lab.rail('snapshot')['orders']
        with pytest.raises(SAACError) as error:
            lab.rail('close_unused', kappa=run['kappa'], proposal=run['proposal'])
        assert error.value.code == 'OUTCOME_UNCERTAIN'
        lab.restart_runtime()
        assert lab.runtime('snapshot')['risk']['budgets'][BUDGET]['reserved'] == 100
        attempt = lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'])
        assert attempt['accepted']
        replay = lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'])
        assert replay['replay'] and replay['execution'] == attempt['execution']
        assert len(lab.rail('snapshot')['orders']) == 1


@pytest.mark.parametrize('iteration', range(3))
def test_remote_non_use_closure_and_redemption_have_one_winner(tmp_path, iteration):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id=f'race:{iteration}')
        barrier = Barrier(2)
        def action(command):
            barrier.wait()
            try:
                return lab.rail(command, kappa=run['kappa'], proposal=run['proposal'])
            except SAACError as error:
                return {'error': error.code}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(action, command) for command in ('close_unused', 'redeem')]
            closed, redeemed = [f.result(timeout=15) for f in futures]
        assert len(lab.rail('snapshot')['executions']) == 1
        receipt = closed.get('receipt', redeemed.get('receipt'))
        assert receipt is not None
        lab.runtime('evidence', receipt=receipt)
        risk = lab.runtime('snapshot')['risk']['budgets'][BUDGET]
        if receipt['result']['status'] == 'non_use':
            assert redeemed['error'] == 'REPLAY'
            assert risk['reserved'] == 0
            assert lab.rail('snapshot')['orders'] == []
        else:
            assert redeemed['accepted']
            assert risk['reserved'] == 100
            assert len(lab.rail('snapshot')['orders']) == 1


@pytest.mark.parametrize('mutation,code', [('signature', 'INVALID_SIGNATURE'), ('effect', 'EFFECT_MISMATCH'), ('audience', 'WRONG_AUDIENCE'), ('identity', 'IDENTITY_BINDING')])
def test_gateway_refuses_changed_authority_or_effect(tmp_path, mutation, code):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id='binding')
        kappa, p = deepcopy(run['kappa']), deepcopy(run['proposal'])
        if mutation == 'signature': kappa['sig'] = 'tampered'
        if mutation == 'effect': p['quantity'] = 2
        if mutation == 'audience': p['socket_id'] = 'OTHER-RAIL'
        if mutation == 'identity': p['agent_id'] = 'different-actor'
        with pytest.raises(SAACError) as error:
            lab.rail('redeem', kappa=kappa, proposal=p)
        assert error.value.code == code
        assert not lab.rail('snapshot')['orders']
        assert lab.runtime('snapshot')['risk']['budgets'][BUDGET]['reserved'] == 100


def test_remote_inbox_rejects_forged_evidence_without_capacity(tmp_path):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id='receipt')
        attempt = lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'])
        bad = deepcopy(attempt['receipt'])
        bad['result']['released'][BUDGET] = 100
        with pytest.raises(SAACError) as error:
            lab.runtime('evidence', receipt=bad)
        assert error.value.code == 'INVALID_SIGNATURE'
        state = lab.runtime('snapshot')
        assert state['risk']['budgets'][BUDGET]['reserved'] == 100 and not state['receipts']
        with pytest.raises(SAACError) as error:
            lab.runtime('unsafe_timeout_release')
        assert error.value.code == 'UNSAFE_CONTROL_DISABLED'


def test_store_ownership_no_shared_connections_or_issuer_keys(tmp_path):
    with RemoteRailLab(tmp_path) as lab:
        assert lab.runtime('health')['pid'] != lab.rail('health')['pid']
        assert (tmp_path / 'A/book-A.sqlite').exists() and (tmp_path / 'B/journal-B.sqlite').exists()
        assert (tmp_path / 'A/keys/runtime.key').exists() and not (tmp_path / 'A/keys/rail.key').exists()
        assert (tmp_path / 'B/keys/rail.key').exists() and not (tmp_path / 'B/keys/runtime.key').exists()
        with pytest.raises(SAACError) as error:
            lab.rail('shell', command_line='anything')
        assert error.value.code == 'REMOTE_COMMAND'


@pytest.mark.parametrize('transition,code', [('expire_authority', 'EXPIRED'), ('halt', 'BREAKER_HALT')])
def test_live_eligibility_changes_refuse_remote_execution(tmp_path, transition, code):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id='eligibility')
        lab.runtime(transition)
        lab.restart_runtime()
        with pytest.raises(SAACError) as error:
            lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'])
        assert error.value.code == code
        assert not lab.rail('snapshot')['orders']
        assert lab.runtime('snapshot')['risk']['budgets'][BUDGET]['reserved'] == 100
        closed = lab.rail('close_unused', kappa=run['kappa'], proposal=run['proposal'])
        assert closed['receipt']['result']['status'] == 'non_use'
        assert lab.runtime('evidence', receipt=closed['receipt'])['applied']
        assert lab.runtime('snapshot')['risk']['budgets'][BUDGET]['reserved'] == 0


def test_c8_independent_verifier_accepts_observation_and_retains_control_failure(observed):
    from saac.coverage_verifier import verify_bundle
    report = verify_bundle(observed)
    assert report['valid'], report
    assert report['negative_control_detected']
    assert report['conformance']['safe']['valid']
    assert not report['conformance']['unsafe']['valid']


@pytest.mark.parametrize('mutation', [
    'attestation_signature', 'receipt_signature', 'capability_lifecycle', 'missing_event', 'replayed_event',
    'runtime_risk', 'rail_order', 'process_pid', 'process_snapshot', 'historical_control_failure',
])
def test_c8_verifier_rejects_tampered_evidence(observed, mutation):
    from saac.coverage_verifier import verify_bundle
    value = deepcopy(observed)
    branch = value['branches']['safe']
    point = branch['checkpoints'][1]
    runtime, rail = point['snapshot']['runtime'], point['snapshot']['rail']
    if mutation == 'attestation_signature':
        runtime['attestation']['sig'] = 'forged'
    elif mutation == 'receipt_signature':
        rail['receipts'][0]['sig'] = 'forged'
    elif mutation == 'capability_lifecycle':
        runtime['reservations'][0]['kappa']['reservation']['id'] = 'different-lifecycle'
    elif mutation == 'missing_event':
        rail['events'].pop(0)
    elif mutation == 'replayed_event':
        rail['events'].append(deepcopy(rail['events'][0]))
    elif mutation == 'runtime_risk':
        runtime['risk']['budgets'][BUDGET]['reserved'] = 0
    elif mutation == 'rail_order':
        rail['orders'][0]['notional'] = 50
    elif mutation == 'process_pid':
        branch['retry']['runtime_pid_after'] = branch['retry']['runtime_pid_before']
    elif mutation == 'process_snapshot':
        runtime['process']['pid'] += 1000
    else:
        value['branches']['unsafe']['ordinary_conformance'] = True
    # An attacker can recompute unsigned wrappers; signed snapshots and the
    # independent journal replay must still reject the changed evidence.
    point['snapshot_digest'] = digest(point['snapshot'])
    report = verify_bundle(value)
    assert not report['valid'], (mutation, report)


def test_checkpoint_attestation_cannot_be_used_as_release_evidence(tmp_path):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id='no-attestation-release')
        attestation = lab.rail('snapshot')['attestation']
        with pytest.raises(SAACError) as error:
            lab.runtime('evidence', receipt=attestation)
        assert error.value.code == 'INVALID_SIGNATURE'
        assert lab.runtime('snapshot')['risk']['budgets'][BUDGET]['reserved'] == run['kappa']['reservation']['bound'][BUDGET]


def test_expired_interrupted_dispatch_stays_in_oracle_and_cannot_close(tmp_path):
    with RemoteRailLab(tmp_path) as lab:
        run = lab.runtime('authorize', proposal=proposal(), request_id='stranded-claim')
        with pytest.raises(SAACError) as error:
            lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'], interrupt_after_claim=True)
        assert error.value.code == 'INTERRUPTED_DISPATCH'
        lab.runtime('expire_authority')
        oracle = reconstruct_c8(lab.runtime('snapshot'), lab.rail('snapshot'))
        assert oracle['E'] == oracle['unresolved_dispatch_obligation'] == 100
        assert oracle['outstanding_promises'] == oracle['promise_obligation'] == 0
        with pytest.raises(SAACError) as error:
            lab.rail('redeem', kappa=run['kappa'], proposal=run['proposal'])
        assert error.value.code == 'EXPIRED'
        with pytest.raises(SAACError) as error:
            lab.rail('close_unused', kappa=run['kappa'], proposal=run['proposal'])
        assert error.value.code == 'OUTCOME_UNCERTAIN'
        assert lab.runtime('snapshot')['risk']['budgets'][BUDGET]['reserved'] == 100
