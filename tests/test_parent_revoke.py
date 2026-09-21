from copy import deepcopy
import json

import pytest

from saac.coverage_c9 import configure_grants, revoke_parent, run_c9
from saac.coverage_common import SCALE, checkpoint, order_service
from saac.domain_models import OrderProposal
from saac.models import SAACError
from saac.protocol import verify_artifact


@pytest.fixture(scope='module')
def evidence(tmp_path_factory):
    return run_c9(tmp_path_factory.mktemp('c9')/'schedule')


def test_c9_safe_retains_occupancy_then_reconciles_revoked_child(evidence):
    safe = evidence['branches']['safe']
    assert [(c['U'], c['Q'], c['E']) for c in safe['checkpoints']] == [
        (0, 600, 600), (0, 600, 600), (0, 600, 600), (200, 0, 200)]
    assert safe['ordinary_conformance'] and safe['ordinary_tape']['valid']
    assert not safe['historical_violation']
    assert all(c['coverage'] and c['ledger_compliant'] for c in safe['checkpoints'])
    assert all(r['accepted'] for r in safe['late_evidence'])


def test_c9_competitor_is_independently_valid_and_denied_only_for_headroom(evidence):
    for branch in evidence['branches'].values():
        grants = branch['grants']
        assert grants['child']['parent_grant_id'] == grants['parent']['id']
        assert grants['child']['ceilings']['notional_usd_cents'] < grants['parent']['ceilings']['notional_usd_cents']
        assert grants['competitor']['parent_grant_id'] is None
        assert grants['competitor']['chain'] == []
        assert branch['competitor_scope_check']['verdict'] == 'allow'
        assert branch['revoked_new_request']['decision']['code'] == 'GRANT_INACTIVE'
        assert grants['cleanup']['parent_grant_id'] is None
        assert branch['cleanup']['proposal']['grant_id'] == grants['cleanup']['id']
        assert branch['cleanup']['attempt']['accepted']
    assert evidence['branches']['safe']['competitor']['decision']['code'] == 'SESSION_LIMIT'
    assert evidence['branches']['unsafe']['competitor']['status'] == 'authorized'


def test_c9_control_preserves_rejected_signed_late_evidence_and_breach(evidence):
    bad = evidence['branches']['unsafe']
    assert [(c['U'], c['Q'], c['E']) for c in bad['checkpoints']] == [
        (0, 600, 600), (0, 0, 600), (0, 600, 1200), (0, 600, 800)]
    assert not bad['ordinary_conformance'] and not bad['ordinary_tape']['valid']
    assert bad['ordinary_tape']['code'] == 'TAPE_ACCOUNTING'
    assert bad['intended_breach_detected'] and bad['historical_violation']
    assert bad['checkpoints'][1]['coverage_gap'] == 600
    assert not bad['checkpoints'][2]['shared_ceiling_compliant']
    assert not bad['checkpoints'][-1]['coverage']
    for delivery in bad['late_evidence']:
        assert not delivery['accepted']
        assert delivery['code'] == 'RECEIPT_REGRESSION'
        verify_artifact(delivery['receipt'], bad['book']['keys']['socket-1'], 'socket-1', 'saac-receipt')
        assert delivery['receipt'] in bad['book']['receipts']
    rejections = [e['data'] for e in bad['book']['events'] if e['kind'] == 'LATE_EVIDENCE_REJECTED']
    assert rejections == bad['late_evidence']


def test_c9_outcome_is_cumulative_idempotent_and_independent_of_revoked_lineage(tmp_path):
    service = order_service(tmp_path/'service')
    configure_grants(service)
    run = service.authority.propose(OrderProposal(quantity=6, limit_price_cents=SCALE,
        agent_id='actor.child', grant_id='G-CHILD'), request_id='history')
    accepted = service.execute(run['id'])
    revoke_parent(service)
    receipt = service.socket.fill_order(accepted['execution']['result']['order']['id'], 2, SCALE)
    assert service.reconciler.accept(receipt)['applied']
    assert not service.reconciler.accept(receipt)['applied']
    assert not service.reconciler.accept(accepted['attempt']['receipt'])['applied']
    observed = checkpoint(service, 'after-fill')
    assert (observed['U'], observed['Q'], observed['E']) == (200, 400, 600)
    tampered = deepcopy(receipt)
    tampered['result']['consumed']['notional_usd_cents'] = 0
    with pytest.raises(SAACError, match='valid signature'):
        service.reconciler.accept(tampered)
    assert checkpoint(service, 'unchanged')['Q'] == 400


def test_c9_export_is_json_safe_and_does_not_export_private_keys(evidence):
    serialized = json.dumps(evidence)
    assert 'PRIVATE KEY' not in serialized
    assert 'riskbook.key' not in serialized and 'socket.key' not in serialized
    assert evidence['units']['scale'] == 100
    for branch in evidence['branches'].values():
        assert all(c['outstanding_promises'] == 0 for c in branch['checkpoints'])


def test_c9_refuses_to_overwrite_an_evidence_directory(tmp_path):
    existing = tmp_path/'evidence'
    existing.mkdir()
    with pytest.raises(FileExistsError):
        run_c9(existing)
