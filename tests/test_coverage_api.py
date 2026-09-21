"""Boundary tests use a stub runner; schedule conformance has dedicated tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from saac.api import create_app
from saac.public_demo import DemoLimits

BASE = '/api/operator/coverage'
PUBLIC = '/api/demo/coverage'
VISITOR = {'X-SAAC-Demo': '1', 'Origin': 'http://testserver'}


@pytest.fixture
def runner(monkeypatch):
    calls = []
    def run_case(case, directory):
        calls.append((case, directory))
        return {'case': case, 'branches': {}, 'test_only_transport_stub': True,
                'units': {'scale': 100, 'accounting_window': 'test'},
                'provenance': {'source_commit': 'test-only', 'source_dirty': True}}
    monkeypatch.setattr('saac.coverage_experiments.run_case', run_case)
    return calls


def test_coverage_actor_cannot_run_or_read_institutional_experiments(tmp_path, runner):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        operator = {'Authorization': 'Bearer ' + app.state.operator_token}
        actor = {'Authorization': 'Bearer ' + app.state.actor_token}
        result = client.post(BASE+'/C9/run', headers=operator, json={'request_id': 'one'})
        assert result.status_code == 200, result.text
        identifier = result.json()['id']
        for method, path, body in [('GET', BASE, None), ('POST', BASE+'/C9/run', {'request_id': 'two'}),
                                   ('GET', BASE+'/runs/'+identifier, None),
                                   ('GET', BASE+'/runs/'+identifier+'/export', None)]:
            assert client.request(method, path, headers=actor, json=body).status_code == 403
            assert client.request(method, path, json=body).status_code == 401
        assert len(runner) == 1


def test_coverage_requests_accept_only_fixed_cases_and_durable_request_ids(tmp_path, runner):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + app.state.operator_token}
        for extra in ({'command': 'echo x'}, {'directory': '/tmp'}, {'policy': 'unsafe'},
                      {'Q': 0}, {'capability': {}}, {'release': True}):
            assert client.post(BASE+'/C9/run', headers=headers,
                               json={'request_id': 'one', **extra}).status_code == 422
        for case in ('C7', 'c9', 'shell'):
            assert client.post(BASE+'/'+case+'/run', headers=headers,
                               json={'request_id': 'one'}).status_code == 422
        for body in ({}, {'request_id': ''}, {'request_id': 'x'*129}):
            assert client.post(BASE+'/C9/run', headers=headers, json=body).status_code == 422
        assert client.get(BASE+'/runs/unknown', headers=headers).status_code == 409
        assert not runner


def test_coverage_duplicate_requests_execute_once_and_retargeting_conflicts(tmp_path, runner):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + app.state.operator_token}
        def invoke(_):
            response = client.post(BASE+'/C9/run', headers=headers, json={'request_id': 'ambiguous-response'})
            assert response.status_code == 200, response.text
            return response.json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(invoke, range(2)))
        assert first['id'] == second['id']
        assert len(runner) == 1
        assert client.post(BASE+'/C10/run', headers=headers,
                           json={'request_id': 'ambiguous-response'}).status_code == 409
        exported = client.get(BASE+'/runs/'+first['id']+'/export', headers=headers).json()
        assert exported['test_only_transport_stub']
        assert client.get(BASE, headers=headers).json()['runs'] == [
            {key: first[key] for key in ('id', 'case', 'title', 'status')}]


def test_coverage_public_isolation_and_shared_book_quota(tmp_path, runner):
    app = create_app(tmp_path, demo_limits=replace(DemoLimits(), books=3))
    with TestClient(app), TestClient(app, headers=VISITOR) as a, TestClient(app, headers=VISITOR) as b:
        assert a.post('/api/demo/session').status_code == 200
        assert b.post('/api/demo/session').status_code == 200
        first = a.post(PUBLIC+'/C9/run', json={'request_id': 'one'})
        assert first.status_code == 200, first.text
        identifier = first.json()['id']
        for suffix in ('', '/export'):
            assert b.get(PUBLIC+'/runs/'+identifier+suffix).status_code == 409
        assert a.get(BASE).status_code == 401
        assert a.post('/api/demo/workbench/books', json={'profile': 'trading'}).status_code == 200
        assert a.post(PUBLIC+'/C9/run', json={'request_id': 'two'}).status_code == 429
        assert a.post('/api/demo/uncertain', json={}).status_code == 429
        assert a.post('/api/demo/workbench/books', json={'profile': 'trading'}).status_code == 429
        assert a.post(PUBLIC+'/C9/run', json={'request_id': 'one'}).json()['id'] == identifier
        assert b.post(PUBLIC+'/C9/run', json={'request_id': 'one'}).status_code == 200
        assert len(runner) == 2
        assert not app.state.service.view()['reservations']


def test_coverage_public_c8_is_explicitly_unsupported_and_origin_guarded(tmp_path, runner):
    app = create_app(tmp_path)
    with TestClient(app, headers=VISITOR) as client:
        assert client.post('/api/demo/session').status_code == 200
        assert client.post(PUBLIC+'/C8/run', json={'request_id': 'one'}).status_code == 403
        catalog = client.get(PUBLIC).json()
        assert not next(case for case in catalog['cases'] if case['id'] == 'C8')['supported']
        assert client.post(PUBLIC+'/C9/run', json={'request_id': 'one'},
                           headers={'Origin': 'http://other-site'}).status_code == 403
        assert not runner
        headers = {'Authorization': 'Bearer ' + app.state.operator_token}
        assert client.post(BASE+'/C8/run', json={'request_id': 'one'}, headers=headers).status_code == 200
        assert len(runner) == 1


def test_coverage_concurrent_creations_do_not_oversubscribe_public_book_quota(tmp_path, runner):
    app = create_app(tmp_path, demo_limits=replace(DemoLimits(), books=2))
    with TestClient(app, headers=VISITOR) as client:
        assert client.post('/api/demo/session').status_code == 200
        def invoke(identifier):
            return client.post(PUBLIC+'/C9/run', json={'request_id': identifier}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(invoke, ('independent-first', 'independent-second')))
        assert sorted(statuses) == [200, 429]
        assert len(runner) == 1
        assert len(client.get(PUBLIC).json()['runs']) == 1


def test_coverage_heavy_job_budget_and_failed_attempt_are_retained(tmp_path, monkeypatch):
    app = create_app(tmp_path, demo_limits=replace(DemoLimits(), jobs_per_session=1))
    calls = []
    def failed(case, directory):
        calls.append(case)
        raise RuntimeError('Test injection before completion')
    monkeypatch.setattr('saac.coverage_experiments.run_case', failed)
    with TestClient(app, headers=VISITOR, raise_server_exceptions=False) as client:
        assert client.post('/api/demo/session').status_code == 200
        assert client.post(PUBLIC+'/C9/run', json={'request_id': 'one'}).status_code == 500
        retry = client.post(PUBLIC+'/C9/run', json={'request_id': 'one'})
        assert retry.status_code == 200
        assert retry.json()['status'] == 'failed'
        assert client.get(PUBLIC+'/runs/'+retry.json()['id']+'/export').status_code == 409
        assert client.post(PUBLIC+'/C9/run', json={'request_id': 'two'}).status_code == 429
        assert calls == ['C9']
        assert app.state.demo_manager.jobs == 0
