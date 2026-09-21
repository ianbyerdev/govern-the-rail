"""Adversarial mutations of captured C9/C10 evidence, including rehashed tapes."""
from copy import deepcopy

import pytest

from saac.coverage_c9 import run_c9
from saac.coverage_c10 import run_c10
from saac.coverage_verifier import verify_bundle
from saac.crypto import digest


@pytest.fixture(scope='module')
def evidence(tmp_path_factory):
    directory = tmp_path_factory.mktemp('coverage-verifier')
    return {'C9': run_c9(directory/'c9'), 'C10': run_c10(directory/'c10')}


def rehash(book):
    previous = 'genesis'
    for seq, event in enumerate(book['events'], 1):
        event.update(seq=seq, prev_hash=previous)
        event['hash'] = digest({k: v for k, v in event.items() if k != 'hash'})
        previous = event['hash']
    book['head'] = previous


def replace_c9_final(bundle, book):
    bundle['branches']['safe']['checkpoints'][-1]['snapshot'] = book
    bundle['branches']['safe']['book'] = deepcopy(book)
    bundle['branches']['safe']['admissions'] = deepcopy(book['runs'])


@pytest.mark.parametrize('case', ('C9', 'C10'))
def test_coverage_verifier_accepts_observed_safe_and_detected_controls(evidence, case):
    result = verify_bundle(evidence[case])
    assert result['valid'], result
    assert result['negative_control_detected']
    assert not result['conformance']['unsafe']['valid']
    assert all(v['valid'] for name, v in result['conformance'].items() if name != 'unsafe')
    assert 'unanchored' in result['trust_boundary']


@pytest.mark.parametrize('mutation', (
    'checkpoint_number', 'clipped_gap', 'snapshot_allocation', 'snapshot_grant',
    'missing_event_rehashed', 'replayed_event_rehashed', 'capability_signature_rehashed',
    'receipt_lifecycle_rehashed', 'late_receipt_projection', 'competitor_projection',
    'cleanup_projection', 'scope_projection', 'negative_control_label', 'missing_attestation', 'oracle_lifecycle',
))
def test_c9_verifier_rejects_mutation_even_with_rehashed_local_history(evidence, mutation):
    bundle = deepcopy(evidence['C9'])
    safe, unsafe = bundle['branches']['safe'], bundle['branches']['unsafe']
    book = deepcopy(safe['book'])
    if mutation == 'checkpoint_number':
        safe['checkpoints'][0]['Q'] = 0
    elif mutation == 'clipped_gap':
        unsafe['checkpoints'][1]['coverage_gap'] = 0
    elif mutation == 'snapshot_allocation':
        next(iter(book['allocations'].values()))['notional_usd_cents']['consumed'] = 0
        replace_c9_final(bundle, book)
    elif mutation == 'snapshot_grant':
        next(g for g in book['grants'] if g['id'] == 'G-COMPETITOR')['ceilings']['notional_usd_cents'] = 9999
        replace_c9_final(bundle, book)
    elif mutation == 'missing_event_rehashed':
        assert book['events'][-1]['kind'] == 'RESERVATION_SETTLED'
        book['events'].pop()
        rehash(book)
        replace_c9_final(bundle, book)
    elif mutation == 'replayed_event_rehashed':
        book['events'].append(deepcopy(book['events'][-1]))
        rehash(book)
        replace_c9_final(bundle, book)
    elif mutation == 'capability_signature_rehashed':
        event = next(e for e in book['events'] if e['kind'] == 'CAPABILITY_ISSUED')
        event['data']['sig'] = 'invalid'
        rehash(book)
        replace_c9_final(bundle, book)
    elif mutation == 'receipt_lifecycle_rehashed':
        event = next(e for e in book['events'] if e['kind'] == 'RECEIPT_CREATED')
        event['data']['kappa_id'] = 'different-lifecycle'
        rehash(book)
        replace_c9_final(bundle, book)
    elif mutation == 'late_receipt_projection':
        unsafe['late_evidence'][0]['receipt']['sig'] = 'invalid'
    elif mutation == 'competitor_projection':
        safe['competitor']['proposal']['grant_id'] = 'G-CHILD'
    elif mutation == 'cleanup_projection':
        safe['cleanup']['proposal']['grant_id'] = 'G-CHILD'
    elif mutation == 'scope_projection':
        safe['competitor_scope_check']['effect']['p'] = 'different-principal'
    elif mutation == 'negative_control_label':
        unsafe['intended_breach_detected'] = False
    elif mutation == 'missing_attestation':
        del book['attestation']
        replace_c9_final(bundle, book)
    elif mutation == 'oracle_lifecycle':
        safe['checkpoints'][0]['oracle']['lifecycles'][0]['reservation_id'] = 'invented-lifecycle'
    result = verify_bundle(bundle)
    assert not result['valid'], (mutation, result)


@pytest.mark.parametrize('mutation', (
    'forged_local_green_number', 'enlarged_shared_limit', 'admission_signature_projection',
    'prime_signature', 'prime_binding', 'prime_missing_acceptance', 'prime_replayed_acceptance',
    'prime_truncated_prefix_as_promise', 'cover_forged_charge', 'cover_missing_event',
    'cover_retarged_lifecycle', 'cover_missing_attestation', 'prime_missing_attestation',
    'negative_control_label', 'ordinary_tape_projection',
))
def test_c10_verifier_rejects_scope_and_artifact_mutations(evidence, mutation):
    bundle = deepcopy(evidence['C10'])
    unsafe, safe = bundle['branches']['unsafe'], bundle['branches']['covering']
    if mutation == 'forged_local_green_number':
        unsafe['checkpoints'][-1]['local_books']['A']['Q'] = 0
    elif mutation == 'enlarged_shared_limit':
        bundle['account']['L_star'] = 2000
    elif mutation == 'admission_signature_projection':
        safe['admissions']['A']['kappa']['sig'] = 'invalid'
    elif mutation == 'prime_signature':
        unsafe['checkpoints'][-1]['snapshot']['rail']['records'][0]['sig'] = 'invalid'
    elif mutation == 'prime_binding':
        unsafe['checkpoints'][-1]['snapshot']['rail']['records'][0]['lifecycle'] = 'B:wrong-capability'
    elif mutation == 'prime_missing_acceptance':
        unsafe['checkpoints'][-1]['snapshot']['rail']['records'].pop()
    elif mutation == 'prime_replayed_acceptance':
        rail = unsafe['checkpoints'][-1]['snapshot']['rail']
        rail['records'].append(deepcopy(rail['records'][-1]))
    elif mutation == 'prime_truncated_prefix_as_promise':
        frame = unsafe['checkpoints'][-1]
        rail = frame['snapshot']['rail']
        rail['records'].pop()
        rail.update(E=1000, head=digest(rail['records'][-1]))
        frame.update(outstanding_promises=1, evidence_domain='issuance_joined_with_prime')
        frame['oracle'].update(outstanding_promises=1, promise_obligation=1000, evidence_domain='issuance_joined_with_prime')
        unsafe['rail'] = deepcopy(rail)
    elif mutation == 'cover_forged_charge':
        safe['checkpoints'][-1]['snapshot']['covering']['Q'] = 0
    elif mutation == 'cover_missing_event':
        safe['checkpoints'][-1]['snapshot']['covering']['events'].pop()
    elif mutation == 'cover_retarged_lifecycle':
        safe['checkpoints'][-1]['snapshot']['covering']['holds'][0]['kappa_id'] = 'different-capability'
    elif mutation == 'cover_missing_attestation':
        del safe['checkpoints'][-1]['snapshot']['covering']['attestation']
    elif mutation == 'prime_missing_attestation':
        del safe['checkpoints'][-1]['snapshot']['rail']['attestation']
    elif mutation == 'negative_control_label':
        unsafe['intended_breach_detected'] = False
    elif mutation == 'ordinary_tape_projection':
        unsafe['ordinary_tapes']['A']['valid'] = False
    result = verify_bundle(bundle)
    assert not result['valid'], (mutation, result)
