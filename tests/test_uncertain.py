"""Deterministic fixtures: concurrency barriers, durable restart and receipts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Barrier, Event

import pytest

from saac.crypto import digest
from saac.domain_models import OrderProposal
from saac.models import SAACError
from saac.protocol import verify_artifact
from saac.risk_book import decode, encode
from saac.service import SAACService
from saac.uncertain import BUDGET, CONFIG, EPOCH, STAGES, UNIT, UncertainExperiment, observe_events


@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    experiment = UncertainExperiment(tmp_path_factory.mktemp('uncertain-complete'))
    experiment.run()
    return experiment


def proposal(actor=0):
    return OrderProposal(quantity=1, limit_price_cents=UNIT, ttl_seconds=30,
                         agent_id=f'actor.uncertain.{actor:03}', grant_id=f'G-UNCERTAIN-{actor:03}')


def test_matched_atomic_admission_and_event_derived_obligations(completed):
    view = completed.view()
    frames = {f['stage']: f for f in view['frames']}
    for policy in ('evidence', 'timeout'):
        panel = frames['admission']['policies'][policy]
        assert panel['admission']['original'] == {'requested': 100, 'admitted': 10, 'denied': 90}
        assert panel['book'] == {'limit': 10, 'consumed': 0, 'reserved': 10, 'available': 0}
        assert panel['rail']['working_orders'] == 0
        assert panel['observer']['obligation_units'] == 0
        assert panel['observer']['outstanding_promises'] == panel['observer']['total_commitment_units'] == 10
        hidden = frames['hidden_fills']['policies'][policy]
        assert hidden['book'] == panel['book']
        assert hidden['rail']['working_orders'] == 6 and hidden['rail']['filled_orders'] == 4
        assert hidden['rail']['withheld_receipts'] == 4
        assert hidden['observer']['executed_units'] == 4 and hidden['observer']['obligation_units'] == 10
    deadline = frames['deadline']['policies']
    assert deadline['evidence']['book']['reserved'] == 10 and deadline['evidence']['book']['available'] == 0
    assert deadline['timeout']['book']['reserved'] == 0 and deadline['timeout']['book']['available'] == 10
    assert deadline['evidence']['reconciliation']['unresolved'] == 10
    assert all(e['age_seconds'] == 1 and e['next_action'] for e in deadline['evidence']['reconciliation']['exceptions'])
    second = frames['second_batch']['policies']
    assert second['evidence']['admission']['new'] == {'requested': 10, 'admitted': 0, 'denied': 10}
    assert second['timeout']['admission']['new'] == {'requested': 10, 'admitted': 10, 'denied': 0}
    assert second['evidence']['observer']['obligation_units'] == 10
    assert second['timeout']['observer']['obligation_units'] == 20
    assert second['timeout']['observer']['breach_units'] == 10
    assert second['timeout']['observer']['modeled_available_units'] == -10  # Never clip negative liability.
    assert all(p['observer']['outstanding_promises'] == 0 for p in second.values())
    recovered = frames['recovered_fills']['policies']['evidence']
    assert recovered['book'] == {'limit': 10, 'consumed': 4, 'reserved': 6, 'available': 0}
    assert recovered['reconciliation']['resolved'] == 4 and recovered['reconciliation']['unresolved'] == 6
    final = view['policies']
    assert final['evidence']['book'] == {'limit': 10, 'consumed': 4, 'reserved': 0, 'available': 6}
    assert final['evidence']['reconciliation']['resolved'] == 10
    assert final['timeout']['observer']['obligation_units'] == 14
    assert final['timeout']['observer']['unaccounted_units'] == 4
    assert final['timeout']['receipt_rejections'] and final['timeout']['invalid_transitions'] == 10
    assert all(p['rail']['withheld_receipts'] == 0 for p in final.values())


def test_export_retains_signed_artifacts_and_oracle_records(completed):
    bundle = completed.export()
    for key, book in bundle['books'].items():
        for run in book['runs']:
            if 'kappa' in run:
                verify_artifact(run['kappa'], book['keys']['riskbook-1'], 'riskbook-1', 'saac-kappa')
        for receipt in book['receipts']:
            verify_artifact(receipt, book['keys']['socket-1'], 'socket-1', 'saac-receipt')
        observed = observe_events(book['events'], bundle['view']['now'])
        assert observed == book['observer']
        assert {o['id']: o for o in observed['orders']} == {o['id']: o for o in book['orders']}
        assert all(r['evidence_class'] == 'experimental_fixture_oracle_observation' for r in observed['records'])
        for frame in bundle['frames']:
            panel = frame['policies'][key]
            events = book['events'][:panel['event_seq']]
            assert events[-1]['hash'] == panel['event_head']
            assert observe_events(events, frame['now'])['obligation_units'] == panel['observer']['obligation_units']
        # Neither exports nor oracle records contain key material or credentials.
        encoded = encode(book)
        assert 'private_key' not in encoded and 'operator_token' not in encoded and 'actor_token' not in encoded
    assert bundle['config']['unit_notional_cents'] == 100


def test_stage_replay_is_idempotent_and_clock_cannot_be_selected(completed):
    before = completed.export()
    for stage in STAGES:
        replay = completed.step(stage)
        assert replay['replay'] and replay['stage'] == 'cancellations'
    assert completed.export() == before
    with pytest.raises(SAACError, match='Unknown fixed experiment stage'):
        completed.step('advance_clock')


def test_durable_reconstruction_after_execution_before_receipt_acceptance(tmp_path):
    """Failure model: service objects reconstructed from SQLite, no new OS process."""
    experiment = UncertainExperiment(tmp_path)
    experiment.step('admission')
    experiment.step('hidden_fills')
    before = experiment.export()
    del experiment
    restored = UncertainExperiment(tmp_path)
    assert restored.view()['stage'] == 'hidden_fills'
    assert all(p['book']['reserved'] == 10 for p in restored.view()['policies'].values())
    assert all(p['rail']['filled_orders'] == 4 for p in restored.view()['policies'].values())
    assert restored.export()['books']['evidence']['receipts'] == before['books']['evidence']['receipts']
    restored.run()
    assert restored.view()['policies']['evidence']['book']['available'] == 6
    # Exactly six *fresh* one-unit requests may use the proven released budget.
    service = restored.services['evidence']
    admitted = [service.authority.propose(proposal(n), request_id=f'reusable:{n}') for n in range(7)]
    assert sum('kappa' in r for r in admitted) == 6
    assert service.view()['metrics'][BUDGET]['available'] == 0


def test_partial_stage_reconstruction_does_not_duplicate_execution(tmp_path, monkeypatch):
    experiment = UncertainExperiment(tmp_path)
    experiment.step('admission')
    service = experiment.services['evidence']
    original = service.reconciler.accept
    def crash_before_acceptance(receipt):
        raise RuntimeError('simulated interruption after socket commit')
    monkeypatch.setattr(service.reconciler, 'accept', crash_before_acceptance)
    with pytest.raises(RuntimeError, match='socket commit'):
        experiment.step('hidden_fills')
    assert len(service.view()['orders']) == 1
    monkeypatch.setattr(service.reconciler, 'accept', original)
    restored = UncertainExperiment(tmp_path)
    restored.step('hidden_fills')
    assert all(len(s.view()['orders']) == 10 for s in restored.services.values())
    assert all(p['book']['reserved'] == 10 for p in restored.view()['policies'].values())


def test_signed_authority_cannot_execute_before_reservation_commits(tmp_path, monkeypatch):
    experiment = UncertainExperiment(tmp_path)
    service = experiment.services['evidence']
    socket_service = SAACService(service.directory, clock=lambda: EPOCH, profile='trading')
    signed, may_commit, redemption_at_lock = Event(), Event(), Event()
    captured = {}
    signer = service.issuer.sign
    def paused_sign(payload):
        artifact = signer(payload)
        if payload['typ'] == 'saac-kappa':
            captured['kappa'] = artifact
            signed.set()
            assert may_commit.wait(10)
        return artifact
    monkeypatch.setattr(service.issuer, 'sign', paused_sign)
    original_transaction = socket_service.book.transaction
    @contextmanager
    def observed_transaction():
        redemption_at_lock.set()
        with original_transaction() as db:
            yield db
    monkeypatch.setattr(socket_service.book, 'transaction', observed_transaction)
    with ThreadPoolExecutor(max_workers=2) as pool:
        admission = pool.submit(service.authority.propose, proposal())
        assert signed.wait(10)
        redemption = pool.submit(socket_service.redeem, proposal(), captured['kappa'])
        assert redemption_at_lock.wait(10)
        with service.book.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 0
        assert not redemption.done()
        may_commit.set()
        assert 'kappa' in admission.result(timeout=10)
        assert redemption.result(timeout=10)['accepted']
    assert len(service.view()['orders']) == 1


def test_expiry_never_cancels_working_orders_and_new_redemption_expires(tmp_path):
    experiment = UncertainExperiment(tmp_path)
    service = experiment.services['evidence']
    live = service.authority.propose(proposal(0))
    unused = service.authority.propose(proposal(1))
    service.execute(live['id'])
    service.advance(31)
    assert service.execute(unused['id'])['attempt']['code'] == 'EXPIRED'
    assert service.view()['orders'][0]['status'] == 'working'
    assert service.view()['metrics'][BUDGET]['reserved'] == 2 * UNIT
    closed = service.close_unused(unused['id'])
    assert closed['receipt']['result']['status'] == 'non_use'
    assert service.view()['metrics'][BUDGET]['reserved'] == UNIT
    with pytest.raises(SAACError, match='non-use'):
        service.close_unused(live['id'])


def test_missing_forged_conflicting_reordered_and_delayed_receipts_do_not_create_headroom(tmp_path):
    experiment = UncertainExperiment(tmp_path)
    service = experiment.services['evidence']
    run = service.authority.propose(proposal())
    accepted = service.execute(run['id'])['receipt']
    order_id = service.get_run(run['id'])['execution']['result']['order']['id']
    filled = service.socket.fill_order(order_id, 1, UNIT)
    service.reconciler.uncertain(run['reservation_id'])
    service.advance(61)
    before = service.view()['metrics'][BUDGET]
    assert before['reserved'] == UNIT and before['used'] == 0
    forged = deepcopy(filled)
    forged['result']['consumed'][BUDGET] = 0
    with pytest.raises(SAACError, match='signature'):
        service.reconciler.accept(forged)
    # Even a locally signed conflicting cumulative revision is not accepted
    # without the original durable socket journal evidence.
    conflict = deepcopy(filled)
    conflict['result']['consumed'][BUDGET] = 0
    conflict = service.socket_signer.sign({k: v for k, v in conflict.items() if k not in ('kid', 'sig')})
    with pytest.raises(SAACError) as error:
        service.reconciler.accept(conflict)
    assert error.value.code == 'RECEIPT_EVIDENCE'
    assert service.view()['metrics'][BUDGET] == before
    assert service.reconciler.accept(filled)['applied']
    after = service.view()['metrics'][BUDGET]
    assert after['used'] == UNIT and after['reserved'] == 0 and after['available'] == before['available']
    assert not service.reconciler.accept(accepted)['applied']
    assert not service.reconciler.accept(filled)['applied']
    assert service.view()['metrics'][BUDGET] == after


@pytest.mark.parametrize('iteration', range(4))
def test_non_use_closure_and_redemption_cannot_both_win(tmp_path, iteration):
    experiment = UncertainExperiment(tmp_path)
    service = experiment.services['evidence']
    run = service.authority.propose(proposal())
    barrier = Barrier(2)
    def close():
        barrier.wait()
        return service.socket.evidence(run['reservation_id'])
    def execute():
        barrier.wait()
        return service.redeem(run['proposal'], run['kappa'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        close_future, execute_future = pool.submit(close), pool.submit(execute)
        receipt, execution = close_future.result(), execute_future.result()
    service.reconciler.accept(receipt)
    if receipt['result']['status'] == 'non_use':
        assert not execution['accepted'] and execution['code'] == 'REPLAY'
        assert not service.view()['orders']
        assert service.view()['metrics'][BUDGET]['reserved'] == 0
    else:
        assert execution['accepted']
        assert receipt['result']['status'] == 'working'
        assert len(service.view()['orders']) == 1
        assert service.view()['metrics'][BUDGET]['reserved'] == UNIT
    with service.book.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 1


def test_stale_reconstructed_instance_refreshes_clock_under_lock(completed):
    stale = UncertainExperiment(completed.directory)
    for service in stale.services.values():
        service.offset = 0  # An instance cached before another process advanced.
    replay = stale.step('admission')
    assert replay['now'] == EPOCH + 64 and replay['replay']
    assert replay['policies'] == completed.view()['policies']
