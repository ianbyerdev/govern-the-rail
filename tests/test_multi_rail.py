from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from saac.service import SAACService
from saac.models import SAACError, Proposal
from saac.domain_models import OrderProposal, CancelProposal, ReferralProposal, JobProposal, DomainPolicy, DelegateProposal, JobScope
from saac.profiles import default_proposal
from saac.protected_socket import ProtectedSocket
from saac.accounting import dimensions
from saac.crypto import verify
from saac.workbench import ExperimentStore, race, probe
from saac.tape import verify_tape
from saac.api import create_app
from saac.runner import LocalRunner, available


def service(tmp_path, profile): return SAACService(tmp_path/profile, profile=profile, clock=lambda: 1800000000)


def issue(svc, proposal=None):
    run = svc.authority.propose(proposal or default_proposal(svc.profile))
    if run['status'] == 'awaiting_approval': run = svc.authority.approve(run['id'], run['snapshot_hash'], True)
    assert 'kappa' in run, run
    return run


def tape(svc):
    with svc.book.transaction() as db: events = svc.book.events(db)
    return verify_tape(events, svc.view()['keys'])


@pytest.mark.parametrize('profile', ['trading', 'referrals', 'runtime'])
def test_preview_is_not_authority_and_actor_counters_are_ignored(tmp_path, profile):
    s = service(tmp_path, profile)
    p = default_proposal(profile).model_copy(update={'actor_risk_observed': {'free_capacity': 999999999}})
    r = s.authority.propose(p, preview=True)
    assert r['status'] == 'preview' and 'kappa' not in r
    assert not s.view()['reservations']
    with pytest.raises(SAACError): s.execute(r['id'])
    r = issue(s, p)
    assert any(v['reserved'] for v in dimensions(s.view()['risk']).values())
    assert tape(s)['valid']


@pytest.mark.parametrize('profile', ['trading', 'referrals', 'runtime'])
def test_exact_audience_expiry_pack_and_replay(tmp_path, profile):
    s = service(tmp_path, profile)
    r = issue(s)
    wrong = ProtectedSocket(s.book, s.issuer.public, s.socket_signer, s.now, 'OTHER-SOCKET')
    p = default_proposal(profile)
    assert wrong.execute(r['kappa'], p)['code'] == 'WRONG_AUDIENCE'
    r2 = s.socket.execute(r['kappa'], p)
    assert r2['accepted']
    assert s.socket.execute(r['kappa'], p)['code'] == 'REPLAY'
    s.reconciler.accept(r2['receipt'])
    assert not s.reconciler.accept(r2['receipt'])['applied']
    other = service(tmp_path/'other', profile)
    assert other.socket.execute(r['kappa'], p)['code'] == 'INVALID_SIGNATURE'
    expired = service(tmp_path/'expiry', profile)
    e = issue(expired, p.model_copy(update={'ttl_seconds': 1}))
    expired.advance(2)
    assert expired.execute(e['id'])['attempt']['code'] == 'EXPIRED'
    assert any(v['reserved'] for v in dimensions(expired.view()['risk']).values())
    assert all(v['reserved'] == 0 for v in dimensions(expired.reconcile(e['id'])['risk_after']).values())
    updated = service(tmp_path/'updated', profile)
    old = issue(updated)
    config = DomainPolicy.model_validate({k:v for k,v in updated.view()['state']['pack'].items() if k in DomainPolicy.model_fields})
    updated.publish(config.model_copy(update={'version': 2}))
    assert updated.execute(old['id'])['attempt']['code'] == 'PACK_NOT_LIVE'


@pytest.mark.parametrize('profile,changes', [('trading', {'quantity': 2001}), ('referrals', {'documents': ['doc-1','doc-2','doc-3','doc-4','doc-5','doc-6','doc-7']}), ('runtime', {'network':['external']})])
def test_modification_and_resource_staleness(tmp_path, profile, changes):
    s = service(tmp_path, profile)
    p = default_proposal(profile)
    r = issue(s, p)
    assert s.execute(r['id'], proposal=p.model_copy(update=changes))['attempt']['code'] == 'EFFECT_MISMATCH'
    assert not s.view()['orders'] and not s.view()['inbox'] and not s.view()['jobs']
    s.change_state('resource')
    assert s.execute(r['id'])['attempt']['code'] == 'STALE_STATE'


def test_trading_working_partial_cancel_and_price_improvement(tmp_path):
    s = service(tmp_path, 'trading'); r = issue(s)
    assert r['kappa']['reservation']['bound'] == {'notional_usd_cents': 8254000}
    run = s.execute(r['id'])
    assert run['receipt']['result']['status'] == 'working'
    assert s.view()['risk']['budgets']['notional_usd_cents']['used'] == 0
    s.fill(r['id'], quantity=400, price_cents=4126)
    risk = s.view()['risk']['budgets']['notional_usd_cents']
    assert (risk['used'], risk['reserved']) == (1650400, 6603200)
    cancelled = s.cancel_order(r['id'])
    assert cancelled['cancel_run']['kappa']['effect']['o'] == 'order.cancel'
    assert cancelled['run']['receipt']['result']['status'] == 'cancelled'
    assert s.view()['risk']['budgets']['notional_usd_cents']['reserved'] == 0
    with pytest.raises(SAACError): s.fill(r['id'], 1)
    assert tape(s)['capabilities_verified'] == 2


def test_cancel_fill_race_never_releases_live_exposure(tmp_path):
    s = service(tmp_path, 'trading'); r = issue(s); s.execute(r['id'])
    order_id = s.get_run(r['id'])['execution']['result']['order']['id']
    cancel = s.authority.propose(CancelProposal(order_id=order_id))
    barrier = Barrier(2)
    def fill():
        barrier.wait()
        try: return s.fill(r['id'], 400)
        except SAACError as exc: return exc.code
    def cancel_now():
        barrier.wait()
        return s.execute(cancel['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(fill), pool.submit(cancel_now)
        fill_result, cancel_result = a.result(), b.result()
    if cancel_result['attempt']['accepted']:
        s.reconcile(r['id'])
        assert fill_result == 'ORDER_TERMINAL'
    else:
        assert cancel_result['attempt']['code'] == 'STALE_STATE'
        assert s.view()['risk']['budgets']['notional_usd_cents']['reserved'] == 6603200
        s.cancel_order(r['id'])
    assert tape(s)['valid']


def test_fill_receipts_reordered_lost_and_cancel_authority_required(tmp_path):
    s = service(tmp_path, 'trading'); r = issue(s); s.execute(r['id'])
    order = s.get_run(r['id'])['execution']['result']['order']
    rho1 = s.socket.fill_order(order['id'], 400, 4127)
    rho2 = s.socket.fill_order(order['id'], 400, 4127)
    s.reconciler.uncertain(r['reservation_id'])
    assert s.view()['risk']['budgets']['notional_usd_cents']['reserved'] == 8254000
    s.reconciler.accept(rho2)
    assert not s.reconciler.accept(rho1)['applied']
    with pytest.raises(SAACError): s.socket.evidence(r['reservation_id'], cancel=True)
    assert tape(s)['valid']


def test_referral_human_snapshot_consent_manifest_and_irreversibility(tmp_path):
    s = service(tmp_path, 'referrals')
    pending = s.authority.propose(ReferralProposal())
    assert pending['status'] == 'awaiting_approval' and not s.view()['reservations']
    with pytest.raises(SAACError): s.authority.approve(pending['id'], 'different', True)
    s.change_state('consent')
    rejected = s.authority.approve(pending['id'], pending['snapshot_hash'], True)
    assert rejected['decision']['code'] == 'STALE_STATE'
    s.change_state('consent')
    r = issue(s)
    s.change_state('document')
    assert s.execute(r['id'])['attempt']['code'] == 'EFFECT_MISMATCH'
    s.reconcile(r['id'])  # Signed non-use closure, not a timer.
    r = issue(s)
    s.execute(r['id'], lose_receipt=True)
    assert len(s.view()['inbox']) == 1
    assert s.view()['risk']['budgets']['records']['reserved'] == 6
    s.change_state('consent')
    s.reconcile(r['id'])
    assert s.view()['risk']['budgets']['records']['used'] == 6
    with pytest.raises(SAACError): s.socket.evidence(r['reservation_id'], cancel=True)
    assert tape(s)['valid']


def test_canonical_aliases_and_patient_substitution(tmp_path):
    s = service(tmp_path, 'trading'); r = issue(s)
    assert s.execute(r['id'], proposal=OrderProposal(instrument=' XYZ.US '))['attempt']['accepted']
    s = service(tmp_path/'ref', 'referrals'); r = issue(s)
    assert s.execute(r['id'], proposal=ReferralProposal(patient='P-205'))['attempt']['code'] == 'PATIENT_BINDING'
    assert s.execute(r['id'], proposal=ReferralProposal(recipient='south-clinic'))['attempt']['code'] in ('STALE_STATE','EFFECT_MISMATCH')
    assert s.execute(r['id'], proposal=ReferralProposal(patient='demo-patient', recipient='north'))['attempt']['accepted']


@pytest.mark.skipif(not available(), reason='Bubblewrap unavailable; runtime fails closed')
def test_real_containment_and_receipt_loss(tmp_path):
    s = service(tmp_path, 'runtime'); r = issue(s)
    run = s.execute(r['id'], lose_receipt=True)
    evidence = run['execution']['result']['evidence']
    kinds = [event['kind'] for event in run['events']]
    assert kinds.index('DISPATCH_INTENT_COMMITTED') < kinds.index('JOB_LAUNCH_CLAIMED') < kinds.index('EXECUTION_COMPLETED')
    assert evidence['returncode'] == 0
    assert all(p['blocked'] for p in evidence['probe_results']['probes'])
    assert (s.directory/'runner'/run['execution']['id']/'workspace/report.txt').exists()
    assert s.view()['risk']['budgets']['job_slots']['reserved'] == 1
    restarted = SAACService(s.directory, clock=lambda: 1800000000)
    restarted.reconcile(r['id'])
    assert restarted.view()['risk']['budgets']['job_slots']['available'] == 1
    assert restarted.view()['risk']['budgets']['job_starts']['used'] == 1
    assert restarted.view()['jobs'][0]['launch_count'] == 1
    assert tape(restarted)['valid']


@pytest.mark.skipif(not available(), reason='Bubblewrap unavailable')
@pytest.mark.parametrize('fault', ['before_dispatch','after_claim','after_run'])
def test_dispatch_crash_recovery_is_conservative(tmp_path, fault):
    s = service(tmp_path, 'runtime'); r = issue(s)
    if fault == 'before_dispatch': s.execute(r['id'], runner_fault=fault)
    else:
        with pytest.raises(SAACError): s.execute(r['id'], runner_fault=fault)
    assert s.view()['risk']['budgets']['job_slots']['reserved'] == 1
    restarted = SAACService(s.directory, clock=lambda: 1800000000)
    if fault == 'after_claim':
        with pytest.raises(SAACError, match='will not be repeated'): restarted.reconcile(r['id'])
        assert restarted.view()['risk']['budgets']['job_slots']['reserved'] == 1
    else:
        restarted.reconcile(r['id'])
        assert restarted.view()['risk']['budgets']['job_slots']['reserved'] == 0
    assert restarted.view()['jobs'][0]['launch_count'] == 1


def test_runtime_no_fallback_and_monotonic_delegation(tmp_path, monkeypatch):
    s = service(tmp_path, 'runtime')
    delegated = probe(s, 'delegate')['run']
    child = delegated['receipt']['result']['child_grant']
    assert child['session_id'] == 'session.demo'
    assert probe(s, 'expand')['blocked']
    r = issue(s, JobProposal(agent_id=child['agent_id'], grant_id=child['id']))
    monkeypatch.setattr('saac.runner.available', lambda: False)
    with pytest.raises(SAACError): s.execute(r['id'])
    assert s.view()['risk']['budgets']['job_slots']['reserved'] == 1
    assert probe(s, 'unsigned')['blocked']
    assert not probe(s, 'unprotected')['blocked']
    assert len(s.view()['unsafe_sink']) == 1
    assert tape(s)['valid']


@pytest.mark.parametrize('profile', ['payments','trading','referrals','runtime'])
def test_hundred_concurrent_requests_all_dimensions(tmp_path, profile):
    store = ExperimentStore(tmp_path, SAACService(tmp_path/'main'))
    result = race(store, profile)
    assert result['race']['accepted'] == 10
    assert result['race']['unsafe']['accepted'] == 100
    assert all(v['used']+v['reserved'] <= v['limit'] for v in result['view']['metrics'].values())
    assert result['race']['verification']['valid']


def test_workbench_api_is_scoped_to_operator_and_real_effects(tmp_path):
    app = create_app(tmp_path); client = TestClient(app)
    actor = {'Authorization': 'Bearer '+app.state.actor_token}
    operator = {'Authorization': 'Bearer '+app.state.operator_token}
    prefix = '/api/operator/workbench'
    for method,path,payload in [('GET','',None), ('POST','/books',{'profile':'trading'}), ('POST','/race/runtime',{})]:
        assert client.request(method,prefix+path,headers=actor,json=payload).status_code == 403
    book = client.post(prefix+'/books', headers=operator, json={'profile':'trading'}).json()['book_id']
    url = prefix+'/books/'+book
    result = client.post(url+'/proposals',headers=operator,json={'proposal':OrderProposal().model_dump()})
    assert result.status_code == 200, result.text
    run = result.json()['run']
    run_url = url+'/runs/'+run['id']
    executed = client.post(run_url+'/execute',headers=operator,json={}).json()
    assert executed['view']['orders'][0]['status'] == 'working'
    assert executed['run']['status'] == 'executed'
    assert client.post(run_url+'/reconcile', headers=operator,json={}).status_code == 200
    assert client.get(url+'/tape',headers=operator).json()['verification']['valid']
    bad = client.post(url+'/proposals',headers=operator,json={'proposal':{'operation':'order.submit','quantity':True}})
    assert bad.status_code == 422


def test_ordinary_actor_reaches_only_the_explicit_dummy_coverage_sink(tmp_path):
    app = create_app(tmp_path); client = TestClient(app)
    headers = {'Authorization': 'Bearer '+app.state.actor_token}
    before = app.state.service.view()['risk']
    blocked = client.post('/api/coverage/protected-attempt',headers=headers).json()
    assert blocked['code'] == 'INVALID_SIGNATURE' and not blocked['accepted']
    admitted = client.post('/api/coverage/unprotected-sink',headers=headers).json()
    assert not admitted['protected'] and admitted['synthetic_only']
    assert (tmp_path/'coverage/dummy-sink.sqlite').exists()
    assert app.state.service.view()['risk'] == before
    assert not app.state.service.view()['payments']


@pytest.mark.parametrize('change', ['expiry','pack','resource','halt'])
def test_queued_job_rechecks_authority_at_launch_and_proves_non_use(tmp_path, change):
    s = service(tmp_path, 'runtime'); r = issue(s)
    s.execute(r['id'],runner_fault='before_dispatch')
    if change == 'expiry': s.advance(121)
    elif change == 'pack':
        config=DomainPolicy.model_validate({k:v for k,v in s.view()['state']['pack'].items() if k in DomainPolicy.model_fields})
        s.publish(config.model_copy(update={'version':2}))
    elif change == 'resource': s.change_state('resource')
    else: s.breaker(True)
    with pytest.raises(SAACError): s.reconcile(r['id'])
    assert s.view()['jobs'][0]['launch_count'] == 0
    assert s.view()['risk']['budgets']['job_slots']['reserved'] == 1
    closed=s.close_unused(r['id'])
    assert closed['receipt']['result']['status']=='non_use'
    assert s.view()['risk']['budgets']['job_slots']['available']==1
    assert s.view()['risk']['budgets']['job_starts']['used']==0
    assert tape(s)['valid']
