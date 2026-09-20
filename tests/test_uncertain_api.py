from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi.testclient import TestClient

from saac.api import create_app
from saac.public_demo import DemoLimits


BASE = '/api/operator/uncertain'
PUBLIC = '/api/demo/uncertain'
VISITOR = {'X-SAAC-Demo': '1', 'Origin': 'http://testserver'}


def test_actor_cannot_select_policy_or_access_experiment_controls(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        operator = {'Authorization': 'Bearer ' + app.state.operator_token}
        actor = {'Authorization': 'Bearer ' + app.state.actor_token}
        created = client.post(BASE, headers=operator, json={})
        assert created.status_code == 200, created.text
        path = BASE + '/' + created.json()['id']
        for method, route, body in [('GET', BASE, None), ('POST', BASE, {}),
                                    ('GET', path, None), ('POST', path+'/run', {}),
                                    ('POST', path+'/step', {'stage': 'deadline'}),
                                    ('GET', path+'/export', None)]:
            assert client.request(method, route, headers=actor, json=body).status_code == 403
        for body in [{'policy': 'timeout'}, {'clock': 1800000061}, {'reconcile_by': 0},
                     {'fill': 4}, {'release': True}, {'observer': 'forged'}]:
            assert client.post(BASE, headers=operator, json=body).status_code == 422
        assert client.post(path+'/step', headers=operator,
                           json={'stage': 'admission', 'policy': 'timeout'}).status_code == 422
        assert client.get(path, headers=operator).json()['completed_stages'] == []


def test_duplicate_creation_and_steps_are_idempotent(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + app.state.operator_token}
        def create(_):
            result = client.post(BASE, headers=headers, json={'request_id': 'same-click'})
            assert result.status_code == 200, result.text
            return result.json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(create, range(2)))
        assert first['id'] == second['id']
        assert len(client.get(BASE, headers=headers).json()['experiments']) == 1
        path = BASE + '/' + first['id']
        def step(_):
            result = client.post(path+'/step', headers=headers, json={'stage': 'admission'})
            assert result.status_code == 200, result.text
            return result.json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(step, range(2)))
        for result in results:
            for panel in result['policies'].values():
                assert panel['admission']['original'] == {'requested': 100, 'admitted': 10, 'denied': 90}
        assert len(results[-1]['completed_stages']) == 1
        exported = client.get(path+'/export', headers=headers)
        assert exported.status_code == 200, exported.text
        assert exported.json()['verification']['valid'], exported.json()['verification']
        assert client.post(path+'/step', headers=headers, json={'stage': 'cancellations'}).status_code == 409


def test_visitors_are_isolated_and_comparisons_share_existing_book_quota(tmp_path):
    app = create_app(tmp_path, demo_limits=replace(DemoLimits(), books=3))
    with TestClient(app), TestClient(app, headers=VISITOR) as a, TestClient(app, headers=VISITOR) as b:
        assert a.post('/api/demo/session').status_code == 200
        assert b.post('/api/demo/session').status_code == 200
        first = a.post(PUBLIC, json={'request_id': 'comparison'}).json()
        path = PUBLIC + '/' + first['id']
        assert b.get(path).status_code == 409
        assert b.post(path+'/step', json={'stage': 'admission'}).status_code == 409
        assert b.get(path+'/export').status_code == 409
        assert a.get(BASE).status_code == 401
        assert a.post('/api/demo/workbench/books', json={'profile': 'trading'}).status_code == 200
        assert a.post(PUBLIC, json={}).status_code == 429
        assert a.post('/api/demo/workbench/books', json={'profile': 'payments'}).status_code == 429
        assert a.post(PUBLIC, json={'request_id': 'comparison'}).json()['id'] == first['id']
        assert b.post(PUBLIC, json={}).status_code == 200
        assert not app.state.service.view()['reservations']


def test_full_export_verifier_and_view_agree(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + app.state.operator_token}
        identifier = client.post(BASE, json={}, headers=headers).json()['id']
        path = BASE + '/' + identifier
        result = client.post(path+'/run', json={}, headers=headers)
        assert result.status_code == 200, result.text
        final = result.json()
        exported = client.get(path+'/export', headers=headers)
        assert exported.status_code == 200, exported.text
        evidence = exported.json()
        assert evidence['view']['policies'] == final['policies']
        assert evidence['verification']['valid'], evidence['verification']
        assert evidence['verification']['negative_control_detected']
        assert evidence['verification']['conformance']['evidence']['valid']
        assert not evidence['verification']['conformance']['timeout']['valid']
        assert final['policies']['evidence']['book'] == {'limit': 10, 'consumed': 4, 'reserved': 0, 'available': 6}
        assert client.get('/docs/uncertain-execution').status_code == 200
