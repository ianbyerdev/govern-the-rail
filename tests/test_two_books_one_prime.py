from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import json

import pytest

from saac.coverage_c10 import CoveringAllocation, CoveredAuthority, configuration, capture, run_c10
from saac.coverage_common import BUDGET, SCALE
from saac.crypto import digest, verify
from saac.domain_models import OrderProposal
from saac.models import SAACError
from saac.service import SAACService


@pytest.fixture(scope='module')
def evidence(tmp_path_factory):
    return run_c10(tmp_path_factory.mktemp('c10')/'schedule')


def test_c10_separate_configurations_match_real_shared_scope(evidence):
    expected = {'unsafe': (1000, 1000, 2000), 'covering': (1000, 0, 1000), 'split': (600, 400, 1000)}
    for mode, branch in evidence['branches'].items():
        final = branch['checkpoints'][-1]
        assert (final['local_books']['A']['Q'], final['local_books']['B']['Q'], final['E']) == expected[mode]
        assert final['L_star'] == 1000
        assert final['local_ledger_compliant']
        assert all(t['valid'] for t in branch['ordinary_tapes'].values())
        assert final['outstanding_promises'] == 0
        assert branch['ordinary_conformance'] == (mode != 'unsafe')
    bad = evidence['branches']['unsafe']
    assert bad['checkpoints'][-1]['available'] == -1000
    assert bad['checkpoints'][-1]['coverage']  # It is the true ceiling, not aggregate coverage, that fails.
    assert not bad['checkpoints'][-1]['shared_ceiling_compliant']
    assert bad['intended_breach_detected'] and bad['historical_violation']


def test_c10_counts_usable_promises_before_redemption_without_double_counting(evidence):
    for mode, branch in evidence['branches'].items():
        promises, accepted = branch['checkpoints']
        assert promises['E'] == accepted['E']
        assert promises['outstanding_promises'] == (1 if mode == 'covering' else 2)
        assert promises['snapshot']['rail']['records'] == []
        assert accepted['outstanding_promises'] == 0
        assert len(accepted['snapshot']['rail']['records']) == len(accepted['oracle']['lifecycles'])
        assert all(life['evidence_domain'] == 'accepted_prime_execution' for life in accepted['oracle']['lifecycles'])
        if mode != 'unsafe':
            assert accepted['covering']['Q'] == accepted['E']
            assert accepted['E'] == sum(b['Q'] for b in accepted['local_books'].values())


def test_c10_common_cover_denies_before_second_usable_capability(evidence):
    branch = evidence['branches']['covering']
    assert 'kappa' in branch['admissions']['A']
    assert 'kappa' not in branch['admissions']['B']
    assert branch['admissions']['B']['decision']['code'] == 'SHARED_HEADROOM'
    assert not [e for e in branch['books']['B']['events'] if e['kind'] == 'CAPABILITY_ISSUED']
    events = branch['covering']['events']
    assert [e['kind'] for e in events] == ['COVER_CONFIGURED', 'COVER_HELD', 'LOCAL_ISSUANCE_LINKED', 'COVER_DENIED']
    assert events[1]['data']['kappa_id'] is None


def test_c10_real_contention_commits_at_most_ten_units_before_issuance(tmp_path):
    services, cover, prime = configuration(tmp_path, 'covering')
    barrier = Barrier(2)
    def contender(name):
        barrier.wait(timeout=10)
        return name, services[name].authority.propose(OrderProposal(quantity=10, limit_price_cents=SCALE), request_id='race')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(contender, ('A', 'B')))
    winners = [(name, run) for name, run in results.items() if 'kappa' in run]
    assert len(winners) == 1
    assert sum(r['kappa']['reservation']['bound'][BUDGET] for _, r in winners) == 1000
    assert cover.export()['Q'] == 1000
    assert capture(services, cover, prime, 'race-promises')['E'] == 1000
    name, run = winners[0]
    assert services[name].execute(run['id'])['attempt']['accepted']
    assert prime.export()['E'] == 1000


@pytest.mark.parametrize('fault', ('before_local', 'after_local'))
def test_c10_failed_or_ambiguous_issuance_retains_cover_until_same_identity_recovers(tmp_path, fault):
    services, cover, prime = configuration(tmp_path, 'covering')
    proposal = OrderProposal(quantity=10, limit_price_cents=SCALE)
    with pytest.raises(RuntimeError, match='Injected'):
        services['A'].authority.propose(proposal, request_id='recover', fault=fault)
    assert cover.export()['Q'] == 1000
    assert services['B'].authority.propose(proposal, request_id='competitor')['decision']['code'] == 'SHARED_HEADROOM'
    # Reopen both persisted authority and covering allocator, preserving all holds.
    restarted = SAACService(tmp_path/'A', clock=services['A'].base_clock, profile='trading')
    reopened = CoveringAllocation(tmp_path/'cover')
    authority = CoveredAuthority(restarted.authority, reopened, 'A')
    recovered = authority.propose(proposal, request_id='recover')
    repeated = authority.propose(proposal, request_id='recover')
    assert recovered['kappa'] == repeated['kappa']
    assert reopened.export()['Q'] == 1000
    with restarted.book.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] == 1
    # Existing Gateway wiring reads the same durable book after recovery.
    assert services['A'].execute(recovered['id'])['attempt']['accepted']
    assert services['A'].execute(repeated['id'])['attempt']['code'] == 'REPLAY'
    assert len(prime.export()['records']) == 1


def test_c10_definite_denial_releases_only_an_unissued_hold(tmp_path):
    services, cover, _ = configuration(tmp_path, 'covering')
    invalid = OrderProposal(quantity=10, limit_price_cents=SCALE, grant_id='missing')
    denial = services['A'].authority.propose(invalid, request_id='denied')
    assert denial['decision']['code'] == 'UNKNOWN_GRANT'
    assert cover.export()['Q'] == 0
    assert cover.export()['holds'][0]['status'] == 'released'
    retried = services['A'].authority.propose(invalid, request_id='denied')
    assert retried['id'] == denial['id'] and retried['covering_hold'] == denial['covering_hold']
    assert sum(e['kind'] == 'UNISSUED_COVER_RELEASED' for e in cover.export()['events']) == 1
    permitted = services['B'].authority.propose(OrderProposal(quantity=10, limit_price_cents=SCALE), request_id='available')
    assert 'kappa' in permitted
    assert cover.export()['Q'] == 1000


def test_c10_covering_retry_cannot_change_the_effect(tmp_path):
    services, cover, _ = configuration(tmp_path, 'covering')
    services['A'].authority.propose(OrderProposal(quantity=6, limit_price_cents=SCALE), request_id='same')
    with pytest.raises(SAACError) as error:
        services['A'].authority.propose(OrderProposal(quantity=4, limit_price_cents=SCALE), request_id='same')
    assert error.value.code == 'IDEMPOTENCY_CONFLICT'
    assert cover.export()['Q'] == 600


def test_c10_conserved_split_is_enforced_before_issuance(tmp_path):
    services, cover, prime = configuration(tmp_path, 'split')
    assert cover.config['split'] == {'A': 600, 'B': 400}
    assert sum(cover.config['split'].values()) == cover.config['account']['L_star']
    too_large = services['A'].authority.propose(OrderProposal(quantity=7, limit_price_cents=SCALE), request_id='too-big')
    assert too_large['decision']['code'] == 'SHARED_HEADROOM' and 'kappa' not in too_large
    for name, quantity in (('A', 6), ('B', 4)):
        run = services[name].authority.propose(OrderProposal(quantity=quantity, limit_price_cents=SCALE), request_id='assigned')
        assert services[name].execute(run['id'])['attempt']['accepted']
        extra = services[name].authority.propose(OrderProposal(quantity=1, limit_price_cents=SCALE), request_id='extra')
        assert extra['decision']['code'] == 'SHARED_HEADROOM'
    assert prime.export()['E'] == 1000
    with pytest.raises(SAACError) as error:
        CoveringAllocation(tmp_path/'invalid-cover', {'A': 1000, 'B': 1000})
    assert error.value.code == 'SPLIT_CONSERVATION'


def test_c10_prime_rejects_local_authority_without_common_cover(tmp_path):
    services, cover, prime = configuration(tmp_path, 'covering')
    # Simulate incorrect wiring that bypasses shared allocation at issuance.
    local = services['A'].authority.inner.propose(OrderProposal(quantity=10, limit_price_cents=SCALE), request_id='uncovered')
    result = services['A'].execute(local['id'])
    assert result['attempt']['code'] == 'COVER_MISSING'
    assert not prime.export()['records']
    with services['A'].book.connect() as db:
        assert services['A'].book.risk(db)['budgets'][BUDGET]['reserved'] == 1000
    assert cover.export()['Q'] == 0


def test_c10_downstream_acceptance_survives_local_rollback_retry_without_duplicate_effect(tmp_path):
    services, cover, prime = configuration(tmp_path, 'covering')
    service = services['A']
    run = service.authority.propose(OrderProposal(quantity=10, limit_price_cents=SCALE), request_id='rollback')
    rail = service.socket.rails['order.submit']

    class FailAfterDownstreamAcceptance:
        def execute(self, db, effect, execution_id, mode):
            rail.execute(db, effect, execution_id, mode)
            raise RuntimeError('Injected local failure after durable downstream acceptance')

    service.socket.rails['order.submit'] = FailAfterDownstreamAcceptance()
    with pytest.raises(RuntimeError, match='after durable downstream acceptance'):
        service.execute(run['id'])
    original = prime.export()['records'][0]
    assert prime.export()['E'] == 1000 and cover.export()['Q'] == 1000
    with service.book.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
        assert db.execute('SELECT status FROM nonces').fetchone()[0] == 'unused'
        assert service.book.risk(db)['budgets'][BUDGET]['reserved'] == 1000
    unresolved = capture(services, cover, prime, 'accepted-but-local-rolled-back')
    assert unresolved['E'] == 1000 and unresolved['coverage']
    assert unresolved['outstanding_promises'] == 0  # The prime supersedes the apparent local promise.
    service.socket.rails['order.submit'] = rail
    recovered = service.execute(run['id'])
    assert recovered['attempt']['accepted']
    assert prime.export()['records'] == [original]
    result = recovered['attempt']['receipt']['result']
    assert result['order']['id'] == original['order']['id']
    assert result['prime_execution_id'] == original['order']['execution_id']
    assert result['prime_acceptance_hash'] == digest(original)
    assert result['order']['execution_id'] != original['order']['execution_id']
    assert result['order']['execution_id'] == recovered['execution']['id']
    assert service.execute(run['id'])['attempt']['code'] == 'REPLAY'
    final = capture(services, cover, prime, 'recovered')
    assert final['E'] == final['Q'] == final['covering']['Q'] == 1000
    assert len(final['oracle']['lifecycles']) == 1 and final['outstanding_promises'] == 0
    verify(original, prime.signer.public, 'prime-1', 'coverage-prime-acceptance')
    with service.book.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1


def test_c10_signed_prime_and_cover_records_export_no_private_material(evidence):
    for branch in evidence['branches'].values():
        rail = branch['rail']
        verify(rail['config'], rail['keys']['prime-1'], 'prime-1', 'coverage-prime-config')
        for record in rail['records']:
            verify(record, rail['keys']['prime-1'], 'prime-1', 'coverage-prime-acceptance')
        cover = branch['covering']
        if cover:
            verify(cover['config'], cover['keys']['cover-1'], 'cover-1', 'coverage-cover-config')
            for event in cover['events']:
                verify(event, cover['keys']['cover-1'], 'cover-1', 'coverage-cover-event')
    serialized = json.dumps(evidence)
    assert 'PRIVATE KEY' not in serialized
    assert '.key' not in serialized


def test_c10_refuses_to_overwrite_evidence(tmp_path):
    with pytest.raises(FileExistsError):
        run_c10(tmp_path)
