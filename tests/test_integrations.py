from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from saac.adapters import AgentHarness, MCPAdapter, MODES, propose_via
from saac.api import create_app
from saac.crypto import verify
from saac.demonstrations import demonstrate
from saac.models import SAACError, Proposal
from saac.sdk import HTTPTransport, Identity, SAACClient
from saac.service import SAACService, ServiceTransport
from saac.tape import verify_tape
from saac.workbench import ExperimentStore, race


@pytest.fixture
def actor_api(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, headers={'Authorization': 'Bearer '+app.state.actor_token}) as http:
        yield app.state.service, http, SAACClient(HTTPTransport(http), Identity())


def test_three_integrations_share_authority_and_explicit_receipts(actor_api):
    svc, http, sdk = actor_api
    runs = [propose_via(mode, sdk, {'amount_cents': 7500}) for mode in MODES]
    assert svc.view()['risk']['reserved_cents'] == 22500
    assert len({r['kappa']['kappa_id'] for r in runs}) == 3
    assert [r['proposal']['harness_id'] for r in runs] == ['native-app', 'agent-harness', 'mcp-adapter']
    for run in runs:
        kappa = run['kappa']
        assert kappa['kappa_id'] != kappa['nonce']
        verify(kappa, svc.issuer.public, 'riskbook-1', 'saac-kappa')
        result = sdk.execute(run['proposal'], kappa)
        rho = result['receipt']
        verify(rho, svc.socket_signer.public, 'socket-1', 'saac-receipt')
        assert rho['kappa_id'] == kappa['kappa_id']
        assert rho['nonce'] == kappa['nonce']
        assert rho['reservation_id'] == run['reservation_id']
        assert 'receipt' not in svc.get_run(run['id'])  # Execution is not reconciliation.
        assert sdk.reconcile(rho) == {'applied': True}
        assert sdk.reconcile(rho) == {'applied': False}
    assert svc.view()['risk']['used_cents'] == 22500
    assert svc.view()['risk']['reserved_cents'] == 0
    assert http.put('/api/operator/pack', json={}).status_code == 403
    assert http.post('/api/operator/workbench/books', json={'profile': 'payments'}).status_code == 403


def test_compromised_adapter_cannot_bypass_raw_rail(actor_api):
    svc, http, sdk = actor_api
    proposal = {'amount_cents': 7500}
    # Skip ALL SDK, harness and MCP code. A normal bearer credential is insufficient.
    for payload in ({'proposal': proposal}, {'proposal': proposal, 'kappa': {'allowed': True}}):
        refusal = http.post('/api/rail/execute', json=payload)
        assert refusal.status_code == 200
        assert refusal.json()['code'] == 'INVALID_SIGNATURE'
    assert not svc.view()['payments']
    run = sdk.propose(proposal)
    changed = {**run['proposal'], 'beneficiary': 'community'}
    assert http.post('/api/rail/execute', json={'proposal': changed, 'kappa': run['kappa']}).json()['code'] == 'EFFECT_MISMATCH'
    forged = deepcopy(run['kappa']); forged['effect']['x']['amount_cents'] = 1
    assert sdk.execute(run['proposal'], forged)['code'] == 'INVALID_SIGNATURE'
    result = http.post('/api/rail/execute', json={'proposal': run['proposal'], 'kappa': run['kappa']}).json()
    assert result['accepted']
    assert sdk.execute(run['proposal'], run['kappa'])['code'] == 'REPLAY'
    assert len(svc.view()['payments']) == 1
    # A plausible but unsigned receipt cannot release the reserved exposure.
    fake_receipt = {**result['receipt'], 'sig': 'forged'}
    assert http.post('/api/receipts', json={'receipt': fake_receipt}).status_code == 409
    assert svc.view()['risk']['reserved_cents'] == 7500
    assert sdk.reconcile(result['receipt'])['applied']


def test_server_identity_does_not_depend_on_sdk_checks(actor_api):
    _, http, _ = actor_api
    for field in ('agent_id', 'principal_id', 'grant_id'):
        p = {field: 'forged'}
        assert http.post('/api/proposals', json=p).json()['code'] == 'IDENTITY_BINDING'
        assert http.post('/api/rail/execute', json={'proposal': p}).json()['code'] == 'IDENTITY_BINDING'


def test_mcp_tools_only_route_and_cannot_self_approve(svc):
    sdk = SAACClient(ServiceTransport(svc))
    mcp = MCPAdapter(sdk)
    assert {t['name'] for t in mcp.list_tools()['tools']} == {'saac.propose', 'saac.execute', 'saac.reconcile'}
    preview = mcp.call_tool('saac.propose', {'intent': {}, 'preview': True})['structuredContent']
    assert 'kappa' not in preview and svc.view()['risk']['reserved_cents'] == 0
    refusal = mcp.call_tool('saac.execute', {'proposal': {}, 'kappa': {'allowed': True}})
    assert refusal['isError'] and refusal['structuredContent']['code'] == 'INVALID_SIGNATURE'
    for name, arguments in [('saac.approve', {}), ('saac.propose', {'intent': {}, 'approved': True}),
                             ('saac.execute', {'proposal': 'shell command', 'kappa': None})]:
        with pytest.raises(SAACError): mcp.call_tool(name, arguments)
    with pytest.raises(SAACError): AgentHarness(sdk).propose({'name': 'run_shell', 'arguments': {}})
    run = mcp.call_tool('saac.propose', {'intent': {}})['structuredContent']
    result = mcp.call_tool('saac.execute', {'proposal': run['proposal'], 'kappa': run['kappa']})
    assert not result['isError']
    assert mcp.call_tool('saac.reconcile', {'receipt': result['structuredContent']['receipt']})['structuredContent']['applied']


@pytest.mark.parametrize('artifact', [None, [], 'allowed', 42, True, {'kid': 'riskbook-1', 'typ': 'saac-kappa', 'sig': None}])
def test_malformed_capability_has_structured_refusal(svc, artifact):
    assert svc.socket.execute(artifact, Proposal())['code'] == 'INVALID_SIGNATURE'
    assert not svc.view()['payments']


@pytest.mark.parametrize('status', ['released', 'settled', 'executing'])
def test_live_nonce_alone_cannot_redeem_inactive_reservation(svc, status):
    run = svc.authority.propose(Proposal())
    # Deliberately inconsistent durable state: the explicit reservation check must
    # still refuse even when a nonce happens to be marked unused.
    with svc.book.transaction() as db:
        db.execute('UPDATE reservations SET status=? WHERE id=?', (status, run['reservation_id']))
    assert svc.socket.execute(run['kappa'], Proposal())['code'] == 'RESERVATION_INACTIVE'
    assert not svc.view()['payments']


def test_nonce_binds_receipts_when_optional_capability_id_is_absent(svc, monkeypatch):
    sign = svc.issuer.sign
    def without_optional_id(payload):
        return sign({k: v for k, v in payload.items() if k != 'kappa_id'})
    monkeypatch.setattr(svc.issuer, 'sign', without_optional_id)
    run = svc.authority.propose(Proposal())
    result = svc.execute(run['id'])
    assert result['receipt']['kappa_id'] == run['kappa']['nonce']
    with svc.book.connect() as db: events = svc.book.events(db)
    assert verify_tape(events, svc.view()['keys'])['valid']


@pytest.mark.parametrize('scenario,code,effects', [
    ('normal', 'EXECUTED', 1), ('rejection', 'ACTION_LIMIT', 0),
    ('bypass', 'EXECUTED', 1), ('replay', 'REPLAY', 1),
    ('mutation', 'EFFECT_MISMATCH', 0), ('expiry', 'EXPIRED', 0),
    ('retry', 'REPLAY', 1),
])
def test_visible_scenarios_use_real_core(tmp_path, scenario, code, effects):
    store = ExperimentStore(tmp_path, SAACService(tmp_path/'main'))
    result = demonstrate(store, scenario, 'harness')
    assert result['demonstration']['attempts'][-1]['code'] == code
    assert len(result['view']['payments']) == effects
    if scenario == 'bypass':
        assert result['demonstration']['attempts'][0]['code'] == 'INVALID_SIGNATURE'
    if scenario in ('mutation', 'expiry'):
        assert result['view']['risk']['reserved_cents'] == 100_000


def test_delegation_tree_cannot_manufacture_authority(tmp_path):
    store = ExperimentStore(tmp_path, SAACService(tmp_path/'main'))
    result = demonstrate(store, 'delegation', 'mcp')
    grants = {g['id']: g for g in result['view']['grants']}
    assert len({g['session_id'] for g in grants.values()}) == 1
    for g in grants.values():
        if g['parent_grant_id']:
            parent = grants[g['parent_grant_id']]
            assert g['max_amount_cents'] <= parent['max_amount_cents']
            assert set(g['operations']) <= set(parent['operations'])
            assert set(g['resources']) <= set(parent['resources'])
    assert [a['code'] for a in result['demonstration']['attempts'][-4:]] == [
        'ACTION_LIMIT', 'GRANT_SCOPE', 'BENEFICIARY_DENIED', 'DELEGATION_EXPANSION']
    assert len(result['view']['payments']) == 2
    assert result['view']['risk']['used_cents'] == 400_000


def test_million_dollar_swarm_reserves_one_shared_book(tmp_path):
    store = ExperimentStore(tmp_path, SAACService(tmp_path/'main'))
    result = race(store, 'payments')  # 100 real threads, Barrier(100), 100 identities.
    assert result['race']['accepted'] == 10 and result['race']['denied'] == 90
    assert result['view']['risk']['reserved_cents'] == 100_000_000
    assert len({r['proposal']['agent_id'] for r in result['view']['runs']}) == 100
    assert result['race']['unsafe']['exposure_cents'] == 1_000_000_000
    svc = store.get(result['book_id'])
    with svc.book.connect() as db: events = svc.book.events(db)
    for e in events:
        if e['kind'] == 'RISK_RESERVED':
            risk = e['data']['risk_before']
            assert risk['used_cents']+risk['reserved_cents']+e['data']['bound'] <= 100_000_000
    # Redeem the winners concurrently too: effects plus pending receipts never
    # allow an eleventh reservation or turn $1M into per-agent copies.
    winners = [r for r in result['view']['runs'] if 'kappa' in r]
    barrier = Barrier(10)
    def execute(r):
        barrier.wait(timeout=20)
        return svc.execute(r['id'])
    with ThreadPoolExecutor(max_workers=10) as pool: list(pool.map(execute, winners))
    assert svc.view()['risk']['used_cents'] == 100_000_000
    assert svc.authority.propose(Proposal(amount_cents=1))['decision']['code'] == 'SESSION_LIMIT'


def test_naive_mode_never_mints_authority_or_touches_protected_effects(tmp_path):
    store = ExperimentStore(tmp_path, SAACService(tmp_path/'main'))
    result = race(store, 'payments', 'naive')
    assert result['race']['unsafe']['accepted'] == 100
    assert result['view']['reservations'] == result['view']['payments'] == []
    assert result['run'] is None


@pytest.mark.parametrize('profile', ['payments', 'trading', 'referrals', 'runtime'])
def test_each_domain_refuses_direct_bypass(tmp_path, profile):
    from saac.workbench import probe
    svc = SAACService(tmp_path/profile, profile=profile)
    assert probe(svc, 'unsigned')['result']['code'] == 'INVALID_SIGNATURE'
    view = svc.view()
    assert not any(view[k] for k in ('payments', 'orders', 'inbox', 'jobs'))
