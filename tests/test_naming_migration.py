"""Canonical names preserve allocation identity and deployment boundaries."""
from dataclasses import replace
import gc
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agentic_risc.adapters import MCPAdapter
from agentic_risc.api import create_app
from agentic_risc.config import setting
from agentic_risc.crypto import b64, canonical, unb64
from agentic_risc.models import Proposal, RISCError
from agentic_risc.protocol import verify_artifact
from agentic_risc.public_demo import COOKIE, PREFIX, DemoLimits
from agentic_risc.sdk import RISCClient
from agentic_risc.service import RISCService, ServiceTransport
from agentic_risc.wire import BOOK_FORMAT, WIRE_VERSION


CURRENT_HEADERS = {'Origin': 'http://testserver', 'X-Agentic-RISC-Demo': '1'}
UNRELATED_COOKIE = 'unrelated_demo'


def test_configuration_defaults_and_explicit_current_values(monkeypatch):
    monkeypatch.delenv('AGENTIC_RISC_PORT', raising=False)
    monkeypatch.setenv('OTHER_PORT', '8100')
    assert setting('PORT', '8000') == '8000'
    monkeypatch.setenv('AGENTIC_RISC_PORT', '8200')
    assert setting('PORT', '8000') == '8200'
    monkeypatch.setenv('AGENTIC_RISC_PORT', '')
    assert setting('PORT', '8000') == ''


@pytest.mark.parametrize('settings,expected', [
    ({}, ['/state', '0.0.0.0', '/actor-credential/actor.token']),
    ({'AGENTIC_RISC_DATA_DIR': '/current', 'AGENTIC_RISC_HOST': '127.0.0.3',
      'AGENTIC_RISC_ACTOR_TOKEN_FILE': '/current/token'},
     ['/current', '127.0.0.3', '/current/token']),
    ({'AGENTIC_RISC_DATA_DIR': '', 'AGENTIC_RISC_HOST': '',
      'AGENTIC_RISC_ACTOR_TOKEN_FILE': ''}, ['', '', '']),
])
def test_container_defaults_preserve_explicit_state_and_credentials(settings, expected):
    names = ('DATA_DIR', 'HOST', 'ACTOR_TOKEN_FILE')
    environment = {key: value for key, value in os.environ.items()
                   if key not in {'AGENTIC_RISC_' + name for name in names}}
    environment.update(settings)
    entrypoint = Path(__file__).resolve().parents[1] / 'scripts/container_start.sh'
    probe = ('import json, os; print(json.dumps([os.environ["AGENTIC_RISC_" + name] '
             'for name in ("DATA_DIR", "HOST", "ACTOR_TOKEN_FILE")]))')
    result = subprocess.run(['sh', str(entrypoint), sys.executable, '-c', probe],
                            env=environment, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected


def test_mcp_names_share_durable_identity_and_single_use(svc):
    adapter = MCPAdapter(RISCClient(ServiceTransport(svc)))
    assert {tool['name'] for tool in adapter.list_tools()['tools']} == {
        'agentic_risc.propose', 'agentic_risc.execute', 'agentic_risc.reconcile',
    }
    arguments = {'intent': {}, 'request_id': 'adapter-restart'}
    original = adapter.call_tool('agentic_risc.propose', arguments)['structuredContent']
    restarted = RISCService(svc.directory, clock=svc.base_clock)
    current = MCPAdapter(RISCClient(ServiceTransport(restarted)))
    replay = current.call_tool('agentic_risc.propose', arguments)['structuredContent']
    assert replay['id'] == original['id']
    assert replay['kappa'] == original['kappa']
    assert len(restarted.view()['reservations']) == 1
    execution = {'proposal': replay['proposal'], 'kappa': replay['kappa']}
    accepted = current.call_tool('agentic_risc.execute', execution)['structuredContent']
    assert accepted['accepted']
    assert current.call_tool('agentic_risc.execute', execution)['structuredContent']['code'] == 'REPLAY'
    evidence = {'receipt': accepted['receipt']}
    assert current.call_tool('agentic_risc.reconcile', evidence)['structuredContent']['applied']
    assert not current.call_tool('agentic_risc.reconcile', evidence)['structuredContent']['applied']
    assert len(restarted.view()['payments']) == 1


def test_mcp_tools_cannot_expand_arguments_or_authority(svc):
    adapter = MCPAdapter(RISCClient(ServiceTransport(svc)))
    with pytest.raises(RISCError) as error:
        adapter.call_tool('agentic_risc.propose', {'intent': {}, 'approved': True})
    assert error.value.code == 'TOOL_ARGUMENTS'
    with pytest.raises(RISCError) as error:
        adapter.call_tool('agentic_risc.approve', {})
    assert error.value.code == 'TOOL'
    refusal = adapter.call_tool('agentic_risc.execute', {'proposal': {}, 'kappa': None})
    assert refusal['isError']
    assert refusal['structuredContent']['code'] == 'INVALID_SIGNATURE'
    assert not svc.view()['reservations']
    assert not svc.view()['payments']


@pytest.mark.parametrize('name', ['other.propose', 'other.execute', 'other.reconcile',
                                   'agentic_risc.Propose', 'agentic-risc.propose'])
def test_non_current_tool_names_are_rejected_without_allocation(svc, name):
    adapter = MCPAdapter(RISCClient(ServiceTransport(svc)))
    with pytest.raises(RISCError) as error:
        adapter.call_tool(name, {'intent': {}})
    assert error.value.code == 'TOOL'
    assert not svc.view()['reservations']
    assert not svc.view()['payments']


def test_persisted_request_namespace_survives_runtime_restart(svc):
    original = svc.authority.propose(Proposal(), request_id='before-runtime-restart')
    with svc.book.connect() as db:
        persisted_key = db.execute('SELECT id FROM request_keys WHERE run_id=?', (original['id'],)).fetchone()[0]
    assert persisted_key.startswith('agentic-risc-request:')
    restarted = RISCService(svc.directory, clock=svc.base_clock)
    replay = restarted.authority.propose(Proposal(), request_id='before-runtime-restart')
    assert replay['id'] == original['id'] and replay['kappa'] == original['kappa']
    assert len(restarted.view()['reservations']) == 1


@pytest.mark.parametrize('artifact', ['capability', 'receipt'])
@pytest.mark.parametrize('version', [None, '', '0.0.0', '99.0.0', 'missing'])
def test_correctly_signed_non_current_artifact_version_is_rejected(svc, artifact, version):
    run = svc.authority.propose(Proposal())
    if artifact == 'capability':
        payload, signer, typ = run['kappa'], svc.issuer, 'agentic-risc-kappa'
    else:
        payload = svc.socket.execute(run['kappa'], Proposal())['receipt']
        signer, typ = svc.socket_signer, 'agentic-risc-receipt'
    assert payload['agentic_risc_version'] == WIRE_VERSION
    body = {key: value for key, value in payload.items() if key != 'sig'}
    if version == 'missing':
        body.pop('agentic_risc_version')
    else:
        body['agentic_risc_version'] = version
    signed = {**body, 'sig': b64(signer.key.sign(canonical(body)))}
    # The rejection must be semantic: these bytes have a valid signature.
    Ed25519PublicKey.from_public_bytes(unb64(signer.public)).verify(unb64(signed['sig']), canonical(body))
    with pytest.raises(RISCError) as error:
        verify_artifact(signed, signer.public, signer.kid, typ)
    assert error.value.code == 'INVALID_SIGNATURE'


@pytest.mark.parametrize('composition', ['local', 'remote'])
@pytest.mark.parametrize('format_change', ['marker_missing', 'marker_unknown', 'pack_type',
                                          'pack_version', 'pack_version_missing'])
def test_unsupported_persisted_format_retains_book_keys_and_occupancy(tmp_path, composition, format_change):
    from agentic_risc.remote_rail import RemoteRuntime, proposal

    def start():
        if composition == 'remote':
            return RemoteRuntime(tmp_path, rail_public='unused-before-redemption')
        return RISCService(tmp_path, clock=lambda: 1800000000)

    original = start()
    effect = proposal() if composition == 'remote' else Proposal()
    original.authority.propose(effect, request_id='held-before-format-refusal')
    with original.book.transaction() as db:
        occupancy = original.book.risk(db)
        assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] == 1
    with original.book.transaction() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == BOOK_FORMAT
        if format_change.startswith('marker_'):
            marker = 0 if format_change == 'marker_missing' else 999999
            db.execute(f'PRAGMA user_version={marker}')
        else:
            state = original.book.state(db)
            if format_change == 'pack_type':
                state['pack']['typ'] = 'other-pack'
            elif format_change == 'pack_version':
                state['pack']['agentic_risc_version'] = '99.0.0'
            else:
                state['pack'].pop('agentic_risc_version')
            original.book.save_state(db, state)
    gc.collect()
    db = sqlite3.connect(original.book.path)
    try:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        before_dump = tuple(db.iterdump())
        before_marker = db.execute('PRAGMA user_version').fetchone()[0]
    finally:
        db.close()
    before_book = original.book.path.read_bytes()
    before_keys = {path.relative_to(tmp_path): path.read_bytes() for path in (tmp_path / 'keys').iterdir()}
    with pytest.raises(RISCError) as error:
        start()
    assert error.value.code == 'BOOK_FORMAT'
    assert original.book.path.read_bytes() == before_book
    assert {path.relative_to(tmp_path): path.read_bytes() for path in (tmp_path / 'keys').iterdir()} == before_keys
    db = sqlite3.connect(original.book.path)
    try:
        assert tuple(db.iterdump()) == before_dump
        assert db.execute('PRAGMA user_version').fetchone()[0] == before_marker
        assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM request_keys').fetchone()[0] == 1
    finally:
        db.close()
    with original.book.transaction() as db:
        assert original.book.risk(db) == occupancy


@pytest.mark.parametrize('composition', ['local', 'remote', 'rail'])
def test_existing_unmarked_file_is_not_initialized_or_given_signing_keys(tmp_path, composition):
    from agentic_risc.remote_rail import IndependentRail, RemoteRuntime

    filename = {'local': 'risk.sqlite', 'remote': 'book-A.sqlite', 'rail': 'journal-B.sqlite'}[composition]
    path = tmp_path / filename
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE sentinel (value TEXT)')
    db.execute("INSERT INTO sentinel VALUES ('existing obligation')")
    db.commit()
    db.close()
    before = path.read_bytes()
    with pytest.raises(RISCError) as error:
        if composition == 'local':
            RISCService(tmp_path)
        elif composition == 'remote':
            RemoteRuntime(tmp_path, rail_public='unused')
        else:
            IndependentRail(tmp_path, runtime_endpoint='unused', issuer_public='unused')
    assert error.value.code == 'BOOK_FORMAT'
    assert path.read_bytes() == before
    assert set(tmp_path.iterdir()) == {path}


def test_current_cookie_preserves_session_quota_and_revocation(tmp_path):
    app = create_app(tmp_path, demo_limits=replace(DemoLimits(), books=1))
    with TestClient(app) as client:
        initial = client.post(PREFIX + '/session', headers=CURRENT_HEADERS)
        assert initial.status_code == 200
        session = initial.json()
        token = client.cookies.get(COOKIE)
        assert client.post(PREFIX + '/workbench/books', json={'profile': 'payments'}, headers=CURRENT_HEADERS).status_code == 200
        with app.state.demo_manager.db() as db:
            before = dict(db.execute('SELECT * FROM sessions WHERE id=?', (session['session_id'],)).fetchone())
        repeated = client.post(PREFIX + '/session', headers=CURRENT_HEADERS)
        assert repeated.status_code == 200 and repeated.json()['session_id'] == session['session_id']
        assert repeated.json()['expires_at'] == session['expires_at']
        assert client.cookies.get(COOKIE) == token
        with app.state.demo_manager.db() as db:
            after = dict(db.execute('SELECT * FROM sessions WHERE id=?', (session['session_id'],)).fetchone())
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 1
        assert after == before
        assert client.post(PREFIX + '/workbench/books', json={'profile': 'payments'}, headers=CURRENT_HEADERS).status_code == 429
        assert client.get('/api/operator/state').status_code == 401
        assert client.get('/api/operator/state', headers={'Authorization': 'Bearer ' + token}).status_code == 401
        actor = {'Authorization': 'Bearer ' + app.state.actor_token}
        assert client.get('/api/operator/state', headers=actor).status_code == 403
        assert client.post('/api/rail/execute', json={'proposal': {}}).status_code == 401
        ended = client.delete(PREFIX + '/session', headers=CURRENT_HEADERS)
        assert ended.status_code == 200
        assert client.cookies.get(COOKIE) is None
        assert any(value.startswith(COOKIE + '=') and 'Max-Age=0' in value
                   for value in ended.headers.get_list('set-cookie'))
        assert client.get(PREFIX + '/workbench', headers={'Cookie': COOKIE + '=' + token}).status_code == 401


@pytest.mark.parametrize('primary', ['', 'invalid-session-token'])
def test_non_current_cookie_cannot_supply_session_authority(tmp_path, primary):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.post(PREFIX + '/session', headers=CURRENT_HEADERS).status_code == 200
        valid = client.cookies.get(COOKIE)
        client.cookies.clear()
        cookies = {'Cookie': f'{COOKIE}={primary}; {UNRELATED_COOKIE}={valid}'}
        assert client.get(PREFIX + '/workbench', headers=cookies).status_code == 401
        assert client.get(PREFIX + '/workbench', headers={'Cookie': UNRELATED_COOKIE + '=' + valid}).status_code == 401


def test_current_cookie_selects_its_own_workspace(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        first = client.post(PREFIX + '/session', headers=CURRENT_HEADERS).json()
        other = client.cookies.get(COOKIE)
        client.cookies.clear()
        second = client.post(PREFIX + '/session', headers=CURRENT_HEADERS).json()
        current = client.cookies.get(COOKIE)
        assert first['session_id'] != second['session_id']
        client.cookies.clear()
        response = client.get(PREFIX + '/session', headers={'Cookie': f'{COOKIE}={current}; {UNRELATED_COOKIE}={other}'})
        assert response.json()['session_id'] == second['session_id']


@pytest.mark.parametrize('primary', ['', '0'])
def test_non_current_header_cannot_authorize_public_mutation(tmp_path, primary):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        unrelated = {'Origin': 'http://testserver', 'X-Unrelated-Demo': '1'}
        assert client.post(PREFIX + '/session', headers=unrelated).status_code == 403
        assert client.post(PREFIX + '/session', headers={**unrelated, 'X-Agentic-RISC-Demo': primary}).status_code == 403
        assert client.post(PREFIX + '/session', headers={**CURRENT_HEADERS, 'X-Unrelated-Demo': '0'}).status_code == 200
        client.cookies.clear()
        assert client.post(PREFIX + '/session', headers={**CURRENT_HEADERS, 'Origin': 'https://other.example'}).status_code == 403
        assert client.post(PREFIX + '/session', headers={**CURRENT_HEADERS, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
