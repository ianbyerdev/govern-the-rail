"""Shared authorization, durable retries, and exact artifact verification."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from saac.adapters import MCPAdapter, MODES, propose_via
from saac.api import create_app
from saac.crypto import digest
from saac.models import SAACError, Proposal
from saac.protocol import verify_artifact
from saac.sdk import SAACClient, HTTPTransport, Identity
from saac.service import SAACService, ServiceTransport
from saac.tape import verify_tape


def test_concurrent_authorization_retries_across_integrations_and_service_instances(svc):
    # Distinct authority instances share durable storage, not a Python nonce cache.
    replica = SAACService(svc.directory, clock=svc.base_clock)
    clients = [SAACClient(ServiceTransport(s), Identity()) for s in (svc, replica)]
    barrier = Barrier(24)

    def work(i):
        barrier.wait(timeout=20)
        return propose_via(MODES[i % 3], clients[i % 2], {'beneficiary': ['acme', 'vendor-001'][i % 2]},
                           request_id='one-lost-response')

    with ThreadPoolExecutor(max_workers=24) as pool:
        runs = list(pool.map(work, range(24)))
    assert len({r['id'] for r in runs}) == 1
    assert len({r['kappa']['sig'] for r in runs}) == 1
    assert len(svc.view()['reservations']) == 1
    assert svc.view()['risk']['reserved_cents'] == 7500
    events = svc.get_run(runs[0]['id'])['events']
    assert sum(e['kind'] == 'RISK_RESERVED' for e in events) == 1
    assert sum(e['kind'] == 'AUTHORIZATION_REPLAYED' for e in events) == 23


def test_http_retry_is_durable_scoped_and_not_new_authority(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, headers={'Authorization': 'Bearer '+app.state.actor_token}) as http:
        sdk = SAACClient(HTTPTransport(http), Identity())
        run = sdk.propose({}, request_id='invoice-123')
        assert run['snapshot']['saac_version'] == '0.3.0'
        assert run['kappa']['typ'] == 'saac-kappa'
        assert http.get('/api/health').json() == {
            'status': 'ok', 'contract': 'SAAC', 'wire_version': '0.3.0', 'rail': 'local simulation',
        }
        # Authentication/identity checks precede idempotency lookup.
        for field in ('principal_id', 'agent_id', 'grant_id'):
            response = http.post('/api/proposals', json={field: 'other'}, headers={'Idempotency-Key': 'invoice-123'})
            assert response.json()['code'] == 'IDENTITY_BINDING'
        for change in ({'beneficiary': 'community'}, {'amount_cents': 7501}, {'ttl_seconds': 121}):
            response = http.post('/api/proposals', json=change, headers={'Idempotency-Key': 'invoice-123'})
            assert response.status_code == 409
            assert response.json()['code'] == 'IDEMPOTENCY_CONFLICT'
        receipt = sdk.execute(run['proposal'], run['kappa'])['receipt']
        assert receipt['typ'] == 'saac-receipt'
        sdk.reconcile(receipt)
    # Service restart and a different adapter cannot mint another allocation.
    restart = create_app(tmp_path)
    with TestClient(restart, headers={'Authorization': 'Bearer '+restart.state.actor_token}) as http:
        sdk = SAACClient(HTTPTransport(http), Identity())
        retried = propose_via('mcp', sdk, {}, request_id='invoice-123')
        assert retried['kappa'] == run['kappa']
        assert sdk.execute(retried['proposal'], retried['kappa'])['code'] == 'REPLAY'
        assert len(restart.state.service.view()['payments']) == 1
        assert len(restart.state.service.view()['reservations']) == 1


def test_request_identity_is_bound_to_authority_context(svc):
    with svc.book.transaction() as db:
        root = svc.book.grant(db, 'G-DEMO')
        peer = {**deepcopy(root), 'id': 'G-PEER', 'agent_id': 'agent.peer'}
        svc.book.save_grant(db, peer)
    first = svc.authority.propose(Proposal(), request_id='same-client-id')
    second = svc.authority.propose(Proposal(agent_id='agent.peer', grant_id='G-PEER'), request_id='same-client-id')
    assert first['id'] != second['id']
    assert svc.view()['risk']['reserved_cents'] == 15000


def test_retry_does_not_refresh_expiry_or_rebind_state(svc):
    p = Proposal(ttl_seconds=1)
    run = svc.authority.propose(p, request_id='expires')
    svc.advance(2)
    retried = svc.authority.propose(p, request_id='expires')
    assert retried['kappa'] == run['kappa']
    assert svc.socket.execute(retried['kappa'], p)['code'] == 'EXPIRED'
    assert svc.view()['risk']['reserved_cents'] == 7500
    run = svc.authority.propose(Proposal(), request_id='stale')
    svc.resource_update(version=2)
    assert svc.authority.propose(Proposal(), request_id='stale')['kappa'] == run['kappa']
    assert svc.socket.execute(run['kappa'], Proposal())['code'] == 'STALE_STATE'
    # An alias change that resolves under a different commitment is not the old request.
    with pytest.raises(SAACError, match='changed canonical effect'):
        svc.authority.propose(Proposal(beneficiary='vendor-001'), request_id='stale')


def test_preview_and_denied_retry_do_not_allocate(svc):
    preview = svc.authority.propose(Proposal(), preview=True, request_id='review')
    assert svc.authority.propose(Proposal(), preview=True, request_id='review')['id'] == preview['id']
    assert svc.view()['risk']['reserved_cents'] == 0
    committed = svc.authority.propose(Proposal(), request_id='review')
    assert committed['id'] != preview['id'] and 'kappa' in committed
    denied = svc.authority.propose(Proposal(amount_cents=100_000_000), request_id='denied')
    assert denied['status'] == 'denied'
    assert svc.authority.propose(Proposal(amount_cents=100_000_000), request_id='denied')['id'] == denied['id']
    assert svc.view()['risk']['reserved_cents'] == 7500


@pytest.mark.parametrize('request_id', ['', ' ', 'bad/key', 'a'*129, '\u00e9', 3, False])
def test_invalid_request_identity_fails_before_allocation(svc, request_id):
    with pytest.raises(SAACError): svc.authority.propose(Proposal(), request_id=request_id)
    assert not svc.view()['reservations']


def test_signed_book_survives_restart_without_rewriting(tmp_path):
    original = SAACService(tmp_path, clock=lambda: 1800000000)
    settled = original.authority.propose(Proposal(amount_cents=100))
    original.execute(settled['id'])
    outstanding = original.authority.propose(Proposal())
    original_pack = original.view()['state']['pack']
    with original.book.connect() as db: prefix = original.book.events(db)
    service = SAACService(tmp_path, clock=lambda: 1800000000)
    assert service.view()['state']['pack'] == original_pack
    assert service.get_run(outstanding['id'])['kappa'] == outstanding['kappa']
    result = service.execute(outstanding['id'])
    assert result['receipt']['typ'] == 'saac-receipt'
    assert result['receipt']['kappa_id'] == outstanding['kappa']['kappa_id']
    fresh = service.authority.propose(Proposal())
    assert fresh['kappa']['typ'] == 'saac-kappa'
    with service.book.connect() as db: events = service.book.events(db)
    assert events[:len(prefix)] == prefix
    assert verify_tape(events, service.view()['keys'])['valid']


def test_artifact_type_substitution_cannot_normalize_or_reuse_signatures(svc):
    payload = svc.authority.propose(Proposal())['kappa']
    renamed = {**payload, 'typ': 'other-kappa'}
    with pytest.raises(SAACError): verify_artifact(renamed, svc.issuer.public, svc.issuer.kid, 'saac-kappa')
    for typ in ('saac-receipt', 'other-receipt', 'unknown-kappa'):
        signed = svc.issuer.sign({**{k: v for k, v in payload.items() if k != 'sig'}, 'typ': typ})
        with pytest.raises(SAACError): verify_artifact(signed, svc.issuer.public, svc.issuer.kid, 'saac-kappa')


def test_campaign_request_keys_and_mcp_tools_preserve_identity(svc):
    p = Proposal()
    run = svc.authority.propose(p)
    with svc.book.transaction() as db:
        db.execute('INSERT INTO request_keys VALUES (?,?,?)', ('channel-request', run['id'], digest(p.model_dump())))
    assert svc.authority.propose(p, idempotency_key='channel-request')['id'] == run['id']
    mcp = MCPAdapter(SAACClient(ServiceTransport(svc)))
    assert all(t['name'].startswith('saac.') for t in mcp.list_tools()['tools'])
    first = mcp.call_tool('saac.propose', {'intent': {}, 'request_id': 'tool-request'})['structuredContent']
    assert mcp.call_tool('saac.propose', {'intent': {}, 'request_id': 'tool-request'})['structuredContent']['id'] == first['id']
    with pytest.raises(SAACError, match='Unknown SAAC tool'):
        mcp.call_tool('other.propose', {'intent': {}})


@pytest.mark.parametrize('non_use', [False, True])
def test_replica_redemption_and_non_use_are_mutually_exclusive(svc, non_use):
    replica = SAACService(svc.directory, clock=svc.base_clock)
    run = svc.authority.propose(Proposal())
    barrier = Barrier(2)
    def execute():
        barrier.wait(timeout=10)
        return svc.socket.execute(run['kappa'], Proposal())
    def competing():
        barrier.wait(timeout=10)
        if non_use: return replica.socket.evidence(run['reservation_id'])
        return replica.socket.execute(run['kappa'], Proposal())
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(execute), pool.submit(competing)
        result, other = a.result(), b.result()
    if non_use:
        assert (other['result']['status'] == 'non_use') == (not result['accepted'])
        receipt = other
    else:
        assert result['accepted'] != other['accepted']
        receipt = (result if result['accepted'] else other)['receipt']
    assert svc.reconciler.accept(receipt)['applied']
    assert not replica.reconciler.accept(receipt)['applied']
    assert len(svc.view()['payments']) == int(receipt['result']['status'] != 'non_use')
    assert svc.view()['risk']['reserved_cents'] == 0


def test_saac_configuration_uses_its_namespace_and_default(monkeypatch):
    from saac.config import setting
    monkeypatch.setenv('OTHER_PORT', '8001')
    monkeypatch.delenv('SAAC_PORT', raising=False)
    assert setting('PORT', '8000') == '8000'
    monkeypatch.setenv('SAAC_PORT', '8002')
    assert setting('PORT') == '8002'


def test_price_improvement_settles_only_actual_exposure(tmp_path):
    from saac.domain_models import OrderProposal
    service = SAACService(tmp_path, clock=lambda: 1800000000, profile='trading')
    run = service.authority.propose(OrderProposal())
    if run['status'] == 'awaiting_approval':
        run = service.authority.approve(run['id'], run['snapshot_hash'], True)
    assert run['kappa']['reservation']['bound'] == {'notional_usd_cents': 8_254_000}
    service.execute(run['id'])
    filled = service.fill(run['id'], quantity=2000, price_cents=4126)
    settlement = filled['receipt']['settlement']
    assert settlement['consumed'] == {'notional_usd_cents': 8_252_000}
    assert settlement['released'] == {'notional_usd_cents': 2_000}
    assert settlement['reservation_status'] == 'settled'
    assert service.view()['risk']['budgets']['notional_usd_cents']['reserved'] == 0
