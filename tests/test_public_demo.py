from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from saac.api import create_app
from saac.models import Proposal
from saac.public_demo import COOKIE, DemoLimits, DemoManager

HEADERS = {'X-SAAC-Demo': '1', 'Origin': 'http://testserver'}
BASE = '/api/demo'


def visitor(app):
    client = TestClient(app, headers=HEADERS)
    result = client.post(BASE + '/session')
    assert result.status_code == 200, result.text
    return client, result.json()


def post(client, path, body=None):
    result = client.post(BASE + path, json=body or {})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.fixture
def host(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app): yield app


def test_start_cookie_is_private_expiring_and_has_no_admin_authority(host):
    client, session = visitor(host)
    assert session['active'] and 'token' not in session
    cookie = next(c for c in client.cookies.jar if c.name == COOKIE)
    assert cookie.path == BASE and cookie.has_nonstandard_attr('HttpOnly')
    assert cookie.get_nonstandard_attr('SameSite') == 'strict'
    assert client.cookies.get(COOKIE) != host.state.operator_token
    with host.state.demo_manager.db() as db:
        stored = db.execute('SELECT token_hash FROM sessions').fetchone()[0]
    assert stored != client.cookies.get(COOKIE)
    assert post(client, '/session')['session_id'] == session['session_id']
    assert client.get(BASE + '/session').json()['session_id'] == session['session_id']
    assert client.get('/api/operator/state').status_code == 401
    assert client.get('/api/operator/workbench').status_code == 401
    assert client.get('/api/operator/swarm').status_code == 401
    assert client.post('/api/rail/execute', json={'proposal': {}}).status_code == 401
    # Even manually presenting the session cookie as a bearer cannot become admin.
    assert client.get('/api/operator/state', headers={'Authorization': 'Bearer ' + client.cookies.get(COOKIE)}).status_code == 401
    assert client.get(BASE + '/workbench').headers['cache-control'] == 'no-store'


def test_cross_visitor_and_admin_books_keys_campaigns_and_actions_are_isolated(host):
    a, sa = visitor(host); b, sb = visitor(host)
    book = post(a, '/workbench/books', {'profile': 'payments'})
    path = '/workbench/books/' + book['book_id']
    run = post(a, path + '/proposals', {'proposal': {}, 'integration': 'mcp'})['run']
    assert b.get(BASE + path).status_code == 409
    assert b.get(BASE + path + '/runs/' + run['id']).status_code == 409
    assert b.post(BASE + path + '/runs/' + run['id'] + '/execute', json={}).status_code == 409
    own = b.get(BASE + '/workbench/books/main').json()
    assert not own['view']['runs']
    assert own['view']['keys'] != book['view']['keys']
    wm = host.state.demo_manager.workspaces[sb['session_id']]
    assert wm.main.socket.execute(run['kappa'], Proposal())['code'] == 'INVALID_SIGNATURE'
    assert host.state.service.view()['runs'] == []
    # Guessing a principal or providing another workspace ID cannot select storage.
    forged = post(b, '/workbench/books/main/proposals', {'proposal': {'principal_id': sa['session_id']}})
    assert 'kappa' not in forged['run']
    c = post(a, '/swarm', {'population': 2})
    assert b.get(BASE + '/swarm/' + c['id']).status_code == 409
    assert not b.get(BASE + '/swarm').json()['campaigns']
    assert a.get('/api/operator/swarm/' + c['id']).status_code == 401


@pytest.mark.parametrize('scenario,code', [('normal', 'EXECUTED'), ('bypass', 'EXECUTED'),
    ('replay', 'REPLAY'), ('mutation', 'EFFECT_MISMATCH'), ('retry', 'REPLAY'), ('expiry', 'EXPIRED')])
def test_public_scenarios_use_the_real_capability_and_receipt_path(host, scenario, code):
    client, _ = visitor(host)
    result = post(client, '/workbench/demonstrations/' + scenario, {'integration': 'harness'})
    assert result['demonstration']['attempts'][-1]['code'] == code
    if scenario == 'bypass': assert result['demonstration']['attempts'][0]['code'] == 'INVALID_SIGNATURE'
    tape = client.get(BASE + '/workbench/books/' + result['book_id'] + '/tape').json()
    assert tape['verification']['valid']


def test_public_payment_swarm_uses_one_shared_million_dollar_book(host):
    client, _ = visitor(host)
    result = post(client, '/workbench/race/payments', {'mode': 'saac'})
    assert result['race']['accepted'] == 10
    assert result['race']['denied'] == 90
    assert result['view']['risk']['reserved_cents'] == 100_000_000
    assert len({g['session_id'] for g in result['view']['grants']}) == 1
    assert host.state.demo_manager.jobs == 0


def test_public_process_launch_and_actor_channel_routes_are_unavailable(host, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Public visitor reached a real runner')
    monkeypatch.setattr('saac.runner.LocalRunner.finish', forbidden)
    monkeypatch.setattr('saac.swarm.workers.contained_proof', forbidden)
    client, _ = visitor(host)
    book = post(client, '/workbench/books', {'profile': 'runtime'})
    path = '/workbench/books/' + book['book_id']
    draft = client.get(BASE + '/workbench').json()['defaults']['runtime']
    run = post(client, path + '/proposals', {'proposal': draft})['run']
    assert 'kappa' in run
    assert client.post(BASE + path + '/runs/' + run['id'] + '/execute', json={}).status_code == 403
    assert not client.get(BASE + path).json()['view']['jobs']
    campaign = post(client, '/swarm', {'population': 2})
    assert client.post(BASE + '/swarm/' + campaign['id'] + '/workers', json={'count': 2}).status_code == 403
    assert client.post(BASE + '/swarm/' + campaign['id'] + '/channels', json={'actor': 'actor-0000'}).status_code == 404


def test_expiry_revocation_cleanup_and_unchanged_admin_history(host):
    manager = host.state.demo_manager
    now = [time.time()]
    manager.clock = lambda: now[0]
    client, session = visitor(host)
    folder = manager.directory / session['session_id']
    post(client, '/workbench/books/main/clock', {'seconds': 3600})
    assert client.get(BASE + '/session').json()['active']  # Scenario clock is not session lifetime.
    now[0] += manager.limits.lifetime_seconds + 1
    assert client.get(BASE + '/workbench').status_code == 401
    manager.cleanup()
    assert not folder.exists()
    assert host.state.service.book.path.exists()
    client, session = visitor(host)
    old_cookie = client.cookies.get(COOKIE)
    assert client.delete(BASE + '/session').status_code == 200
    assert not (manager.directory / session['session_id']).exists()
    client.cookies.set(COOKIE, old_cookie, path=BASE)
    assert client.get(BASE + '/workbench').status_code == 401


def test_session_and_evidence_survive_restart(tmp_path):
    first = create_app(tmp_path)
    with TestClient(first):
        client, session = visitor(first)
        value = post(client, '/workbench/demonstrations/normal')
        cookie = client.cookies.get(COOKIE)
    second = create_app(tmp_path)
    with TestClient(second, headers=HEADERS) as client:
        client.cookies.set(COOKIE, cookie, path=BASE)
        assert client.get(BASE + '/session').json()['session_id'] == session['session_id']
        saved = client.get(BASE + '/workbench/books/' + value['book_id']).json()
        assert saved['view']['runs'][0]['kappa'] == value['run']['kappa']
        assert len(saved['view']['payments']) == 1


@pytest.mark.parametrize('headers', [{}, {'Origin': 'http://evil.example', 'X-SAAC-Demo': '1'},
                                   {'Origin': 'null', 'X-SAAC-Demo': '1'},
                                   {'X-SAAC-Demo': '1', 'Sec-Fetch-Site': 'cross-site'}])
def test_cross_site_session_creation_is_rejected(host, headers):
    client = TestClient(host)
    assert client.post(BASE + '/session', headers=headers).status_code == 403


def test_secure_cookie_and_origin_for_public_https(tmp_path, monkeypatch):
    monkeypatch.setenv('SAAC_PUBLIC_ORIGIN', 'https://demo.example')
    app = create_app(tmp_path)
    with TestClient(app, base_url='https://demo.example') as client:
        result = client.post(BASE + '/session', headers={'Origin': 'https://demo.example', 'X-SAAC-Demo': '1'})
        assert result.status_code == 200
        assert 'Secure' in result.headers['set-cookie'] and 'HttpOnly' in result.headers['set-cookie']
        assert client.post(BASE + '/session', headers=HEADERS).status_code == 403


def test_body_population_book_campaign_and_action_limits(tmp_path):
    limits = replace(DemoLimits(), books=1, campaigns=1, writes_per_session=7, workspace_bytes=10_000_000)
    app = create_app(tmp_path, demo_limits=limits)
    with TestClient(app):
        client, _ = visitor(app)
        assert client.post(BASE + '/workbench/books', content='x'*(limits.body_bytes+1)).status_code == 413
        assert client.post('/api/operator/pack', content='x'*(limits.body_bytes+1)).status_code == 413
        assert client.post(BASE + '/swarm', json={'population': 101}).status_code == 422
        post(client, '/workbench/books', {'profile': 'payments'})
        assert client.post(BASE + '/workbench/books', json={'profile': 'payments'}).status_code == 429
        post(client, '/swarm', {'population': 2})
        assert client.post(BASE + '/swarm', json={'population': 2}).status_code == 429
        post(client, '/workbench/books/main/breaker', {'halted': True})
        post(client, '/workbench/books/main/breaker', {'halted': False})
        assert client.post(BASE + '/workbench/books/main/breaker', json={'halted': True}).status_code == 429
        assert client.get(BASE + '/workbench/books/main/tape').status_code == 200  # Export still works.


def test_storage_and_request_rate_limits(tmp_path):
    app = create_app(tmp_path, demo_limits=replace(DemoLimits(), workspace_bytes=1, requests_per_minute=2))
    with TestClient(app):
        client, _ = visitor(app)
        result = client.post(BASE + '/workbench/books', json={'profile': 'payments'})
        assert result.status_code == 429 and result.json()['code'] == 'DEMO_STORAGE'
        assert client.get(BASE + '/workbench').status_code == 200
        assert client.get(BASE + '/workbench').status_code == 429


def test_concurrent_session_cap_and_creation_throttle(tmp_path):
    manager = DemoManager(tmp_path, limits=replace(DemoLimits(), sessions=2, starts_per_ip=1))
    gate = Barrier(8)
    def create(i):
        gate.wait(timeout=10)
        try: return manager.create(None, str(i))[1]
        except HTTPException as e: return e.status_code
    try:
        with ThreadPoolExecutor(max_workers=8) as pool: results = list(pool.map(create, range(8)))
        assert sum(isinstance(r, dict) for r in results) == 2
        with manager.db() as db: assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 2
    finally: manager.close()


def test_global_job_limit_and_end_during_inflight_campaign(host, monkeypatch):
    manager = host.state.demo_manager
    manager.limits = replace(manager.limits, concurrent_jobs=1)
    a, sa = visitor(host); b, _ = visitor(host)
    c = post(a, '/swarm', {'population': 2})
    workspace = manager.workspaces[sa['session_id']]
    campaign = workspace.campaigns.get(c['id'])
    started, release = Event(), Event()
    process = campaign.process
    def slow(seq):
        started.set()
        assert release.wait(10)
        process(seq)
    monkeypatch.setattr(campaign, 'process', slow)
    try:
        post(a, '/swarm/' + c['id'] + '/control', {'action': 'play'})
        assert started.wait(5)
        assert b.post(BASE + '/workbench/race/payments', json={'mode': 'saac'}).status_code == 429
        assert a.delete(BASE + '/session').status_code == 200
        assert workspace.directory.exists()  # Never remove an in-flight book.
    finally:
        release.set()
        if campaign.thread: campaign.thread.join(10)
    manager.cleanup()
    assert not workspace.directory.exists() and manager.jobs == 0


def test_public_single_process_guard(tmp_path):
    first, second = DemoManager(tmp_path), DemoManager(tmp_path)
    try:
        first.start()
        with pytest.raises(HTTPException) as error: second.start()
        assert error.value.status_code == 503
    finally: first.close(); second.close()


def test_operator_and_actor_remain_separate_from_visitors(host):
    client, _ = visitor(host)
    admin = {'Authorization': 'Bearer '+host.state.operator_token}
    actor = {'Authorization': 'Bearer '+host.state.actor_token}
    assert client.get('/api/operator/session', headers=admin).status_code == 200
    assert client.get('/api/operator/session', headers=actor).status_code == 403
    assert client.get('/api/operator/workbench', headers=admin).status_code == 200
    assert host.state.campaign_store.demo is None


def test_creation_throttle_survives_ending_a_session(tmp_path):
    now = [time.time()]
    manager = DemoManager(tmp_path, limits=replace(DemoLimits(), starts_per_ip=1), clock=lambda: now[0])
    try:
        token, _ = manager.create(None, 'one-peer')
        manager.end(token); manager.cleanup()
        with pytest.raises(HTTPException) as error: manager.create(None, 'one-peer')
        assert error.value.status_code == 429
        now[0] += 601
        assert manager.create(None, 'one-peer')[1]['active']
    finally: manager.close()


def test_heavy_job_budget_and_cleanup_during_http_lease(tmp_path):
    manager = DemoManager(tmp_path, limits=replace(DemoLimits(), jobs_per_session=1))
    try:
        token, _ = manager.create(None, 'peer')
        workspace = manager.enter(token, False)
        with workspace.heavy(): pass
        with pytest.raises(HTTPException):
            with workspace.heavy(): pass
        manager.end(token); manager.cleanup()
        assert workspace.directory.exists()
        manager.leave(workspace); manager.cleanup()
        assert not workspace.directory.exists()
    finally: manager.close()


def test_cookie_alone_cannot_authorize_cross_site_mutation(host):
    client, _ = visitor(host)
    attacker = TestClient(host)
    attacker.cookies.set(COOKIE, client.cookies.get(COOKIE), path=BASE)
    assert attacker.delete(BASE + '/session').status_code == 403
    assert attacker.post(BASE + '/workbench/books/main/breaker', json={'halted': True}).status_code == 403
    assert client.get(BASE + '/session').json()['active']
