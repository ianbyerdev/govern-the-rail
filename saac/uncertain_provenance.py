"""Public, allowlisted provenance; never export workspace secrets or credentials."""
import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import os
import platform
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent


def command(*args):
    try:
        return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
                                       timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def provenance():
    commit = command('git', 'rev-parse', 'HEAD')
    origin = 'git checkout' if commit else 'unavailable'
    # Render documents this non-secret runtime revision at
    # https://render.com/docs/environment-variables#render_git_commit .
    if not commit:
        deployed = os.environ.get('RENDER_GIT_COMMIT', '')
        if re.fullmatch(r'[0-9a-f]{40}', deployed):
            commit, origin = deployed, 'Render runtime revision (no local Git metadata)'
    # Restrict dirty-state identification and hashes to the executable fixture,
    # tests and public documentation. Do not enumerate private local material.
    paths = ['saac', 'scripts', 'tests', 'frontend/src', 'frontend/tests', 'docs',
             'pyproject.toml', 'requirements.lock', 'frontend/package.json',
             'frontend/package-lock.json', 'frontend/index.html', 'frontend/playwright.config.ts',
             'frontend/vite.config.ts', 'frontend/tsconfig.json', 'README.md',
             'Dockerfile', 'Makefile', 'render.yaml', 'compose.yaml', '.dockerignore', '.github']
    status = command('git', 'status', '--porcelain', '--untracked-files=all', '--', *paths)
    worktree_status = command('git', 'status', '--porcelain', '--untracked-files=normal')
    digest = hashlib.sha256()
    for path in sorted(p for item in paths for p in
                       ((ROOT / item).rglob('*') if (ROOT / item).is_dir() else [ROOT / item])
                       if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'):
        # Stored evidence is a later artifact, not a change to executable source.
        if 'uncertain-evidence' in path.parts or 'coverage-evidence' in path.parts:
            continue
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0' + path.read_bytes())
    packages = {}
    for name in ('saac-reference', 'fastapi', 'pydantic', 'cryptography', 'httpx', 'uvicorn', 'pytest'):
        try: packages[name] = version(name)
        except PackageNotFoundError: packages[name] = 'not installed'
    frontend_packages = {}
    lock = ROOT / 'frontend/package-lock.json'
    if lock.exists():
        installed = json.loads(lock.read_text()).get('packages', {})
        for name in ('react', 'react-dom', 'vite', 'typescript', '@playwright/test'):
            frontend_packages[name] = installed.get('node_modules/' + name, {}).get('version')
    return {'source_commit': commit, 'source_origin': origin,
            'source_dirty': bool(worktree_status) if worktree_status is not None else None,
            'source_status': status.splitlines() if status else [],
            'source_tree_sha256': digest.hexdigest(),
            'source_status_scope': paths,
            'fingerprint_scope': 'Available allowlisted fixture files; packaged images can omit tests, frontend sources and build configuration.',
            'revision_claim': 'Local uncommitted source; identify by source tree digest.' if worktree_status else
                              'Clean source checkout.' if worktree_status is not None else
                              'Packaged deployment revision supplied by Render; local dirty status unavailable.' if commit else
                              'Git revision unavailable in this packaged deployment.',
            'runtime': {'python': sys.version, 'node': command('node', '--version'),
                        'npm': command('npm', '--version'), 'packages': packages,
                        'frontend_lock_versions': frontend_packages},
            'os': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
            'ci_run_url': None,
            'secrets_exported': False}
