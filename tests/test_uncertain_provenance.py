import json

from saac import uncertain_provenance as module


def test_packaged_revision_is_allowlisted_and_never_claims_clean_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'command', lambda *args: None)
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'a' * 40)
    monkeypatch.setenv('PROVIDER_SECRET', 'synthetic-secret-never-export')
    value = module.provenance()
    assert value['source_commit'] == 'a' * 40
    assert value['source_dirty'] is None
    assert 'local dirty status unavailable' in value['revision_claim']
    assert 'synthetic-secret-never-export' not in json.dumps(value)


def test_build_and_browser_configuration_changes_alter_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'command', lambda *args: None)
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    config = frontend / 'playwright.config.ts'
    config.write_text('export default { timeout: 1000 }')
    before = module.provenance()['source_tree_sha256']
    config.write_text('export default { timeout: 2000 }')
    assert module.provenance()['source_tree_sha256'] != before
    before = module.provenance()['source_tree_sha256']
    (tmp_path / 'render.yaml').write_text('services: []')
    assert module.provenance()['source_tree_sha256'] != before


def test_invalid_deployment_revision_is_not_exported(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'command', lambda *args: None)
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'not-a-commit')
    assert module.provenance()['source_commit'] is None
