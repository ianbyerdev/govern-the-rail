from copy import deepcopy
import json
import subprocess
import sys

import pytest

from saac.crypto import digest
from saac.uncertain import STAGES, UncertainExperiment
from saac.uncertain_verifier import verify_bundle


@pytest.fixture(scope='module')
def exports(tmp_path_factory):
    experiment = UncertainExperiment(tmp_path_factory.mktemp('uncertain-verifier'))
    snapshots = {'ready': experiment.export()}
    for stage in STAGES:
        experiment.step(stage)
        snapshots[stage] = experiment.export()
    return snapshots


@pytest.mark.parametrize('stage', ('ready', *STAGES))
def test_each_completed_prefix_verifies_without_claiming_an_early_breach(exports, stage):
    report = verify_bundle(exports[stage])
    assert report['valid'], report
    assert report['negative_control_detected'] == (stage in STAGES[3:])
    assert report['conformance']['evidence']['valid']
    if stage in STAGES[2:]:
        assert not report['conformance']['timeout']['valid']
    if stage in STAGES[3:]:
        assert not report['conformance']['timeout']['ordinary_tape']['valid']
    assert all(report['checks'].values())


def rehash_and_reanchor(bundle, policy):
    """Model an attacker who can rewrite the unanchored local event chain."""
    events = bundle['books'][policy]['events']
    previous = 'genesis'
    for event in events:
        event['prev_hash'] = previous
        event['hash'] = digest({key: value for key, value in event.items() if key != 'hash'})
        previous = event['hash']
    for frame in (*bundle['frames'], *bundle['view']['frames']):
        panel = frame['policies'][policy]
        panel['event_head'] = events[panel['event_seq'] - 1]['hash']
    bundle['view']['policies'][policy]['event_head'] = events[-1]['hash']


@pytest.mark.parametrize('kind', ('CAPABILITY_ISSUED', 'RECEIPT_CREATED'))
def test_forged_artifacts_fail_even_after_unsigned_history_is_rehashed(exports, kind):
    bundle = deepcopy(exports['cancellations'])
    event = next(event for event in bundle['books']['evidence']['events'] if event['kind'] == kind)
    event['data']['sig'] = 'forged'
    rehash_and_reanchor(bundle, 'evidence')
    report = verify_bundle(bundle)
    assert not report['valid']
    assert report['errors'][0]['code'] == 'INVALID_SIGNATURE'


def test_rehashed_oracle_fill_must_match_genuine_signed_receipt(exports):
    bundle = deepcopy(exports['cancellations'])
    event = next(event for event in bundle['books']['evidence']['events'] if event['kind'] == 'EMS_FILL')
    event['data']['route'] = 'OTHER-BROKER'
    rehash_and_reanchor(bundle, 'evidence')
    report = verify_bundle(bundle)
    assert not report['valid']
    assert 'matching signed institutional evidence' in report['errors'][0]['message']


def test_rehashed_execution_result_must_match_its_signed_receipt(exports):
    bundle = deepcopy(exports['cancellations'])
    event = next(event for event in bundle['books']['evidence']['events'] if event['kind'] == 'EXECUTION_COMPLETED')
    event['data']['result']['order']['route'] = 'OTHER-BROKER'
    rehash_and_reanchor(bundle, 'evidence')
    report = verify_bundle(bundle)
    assert not report['valid']
    assert 'signed result' in report['errors'][0]['message']


@pytest.mark.parametrize('mutation', ('frame', 'oracle_record', 'omitted_breach', 'order_table',
                                      'nonce', 'request_identity', 'allocation', 'withheld_receipt',
                                      'negative_availability', 'missing_frame'))
def test_export_and_dashboard_tampering_fails_closed(exports, mutation):
    bundle = deepcopy(exports['cancellations'])
    if mutation == 'frame':
        for frames in (bundle['frames'], bundle['view']['frames']):
            frames[2]['policies']['evidence']['book']['available'] = 10
    elif mutation == 'oracle_record':
        bundle['books']['timeout']['observer']['records'][0]['source_event_hash'] = 'sha256:invented'
    elif mutation == 'omitted_breach':
        for frames in (bundle['frames'], bundle['view']['frames']):
            frames[3]['policies']['timeout']['observer']['breach_units'] = 0
    elif mutation == 'order_table':
        bundle['books']['timeout']['orders'][0]['status'] = 'cancelled'
    elif mutation == 'nonce':
        bundle['books']['timeout']['nonces'][0]['status'] = 'unused'
    elif mutation == 'request_identity':
        run = next(run for run in bundle['books']['timeout']['runs'] if run['request_id'].startswith('new:'))
        run['request_id'] = 'original:000'
    elif mutation == 'allocation':
        allocation = next(iter(bundle['books']['evidence']['allocations'].values()))
        allocation['notional_usd_cents']['released'] = 1000
    elif mutation == 'withheld_receipt':
        bundle['books']['evidence']['receipts'].pop()
    elif mutation == 'negative_availability':
        bundle['view']['policies']['timeout']['observer']['modeled_available_units'] = 0
    else:
        bundle['frames'] = bundle['frames'][1:]
        bundle['view']['frames'] = bundle['view']['frames'][1:]
    report = verify_bundle(bundle)
    assert not report['valid'], mutation
    assert report['errors']
    assert not report['negative_control_detected']


@pytest.mark.parametrize('malformed', (None, [], {}, {'schema_version': 1}, {'config': {}, 'view': {}}))
def test_malformed_evidence_is_a_report_not_an_exception(malformed):
    report = verify_bundle(malformed)
    assert not report['valid']
    assert report['errors']


def test_offline_cli_reports_valid_control_detection_and_invalid_json(exports, tmp_path):
    path = tmp_path / 'bundle.json'
    path.write_text(json.dumps(exports['cancellations']))
    completed = subprocess.run([sys.executable, '-m', 'saac.uncertain_verifier', str(path)],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    assert report['valid'] and report['negative_control_detected']
    assert not report['conformance']['timeout']['valid']
    path.write_text('{bad json')
    invalid = subprocess.run([sys.executable, '-m', 'saac.uncertain_verifier', str(path)],
                             capture_output=True, text=True, check=False)
    assert invalid.returncode == 1
    assert not json.loads(invalid.stdout)['valid']
