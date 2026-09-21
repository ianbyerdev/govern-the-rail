"""Fixed coverage schedules, scoped to the authenticated experiment workspace.

The browser selects a schedule, never a command, executable, path, actor, policy,
or counter value. Public C8 execution is deliberately unavailable: the hosted
visitor profile does not authorize starting the separate-process fixture.
"""
from contextlib import nullcontext
import json
from pathlib import Path
import re
from typing import Literal

from fastapi import Depends
from pydantic import Field

from .models import StrictModel, require
from .risk_book import uid


CASES = {
    'C8': {'title': 'Independent rail survives a Runtime restart', 'books': 2},
    'C9': {'title': 'Revoking a parent does not erase a child’s commitment', 'books': 2},
    'C10': {'title': 'Two books, one downstream allocation', 'books': 6},
}


class RunCoverage(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)


def _read(path):
    return json.loads(path.read_text())


def _write(path, value):
    # A reader sees either the preceding complete document or its replacement.
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


class CoverageStore:
    def __init__(self, experiments):
        self.experiments, self.demo = experiments, experiments.demo
        self.directory = experiments.directory.parent / 'coverage'
        self.directory.mkdir(exist_ok=True)

    def folder(self, identifier):
        require(bool(re.fullmatch(r'coverage_[a-f0-9]{16}', identifier)),
                'EXPERIMENT_ID', 'Invalid coverage run identifier.')
        folder = self.directory / identifier
        require((folder / 'request.json').exists(), 'EXPERIMENT_ID',
                'This coverage run does not exist in your workspace.')
        return folder

    def view(self, identifier):
        folder = self.folder(identifier)
        metadata = _read(folder / 'request.json')
        result = {'id': identifier, 'case': metadata['case'],
                  'title': CASES[metadata['case']]['title'],
                  'status': metadata['status'], 'replay': True,
                  'support': 'bounded hosted schedule' if self.demo else 'local operator schedule'}
        if (folder / 'observed.json').exists():
            bundle = _read(folder / 'observed.json')
            result.update({key: bundle[key] for key in
                           ('branches', 'verification', 'provenance', 'units',
                            'domain', 'accounting_window', 'expected', 'expected_vs_observed',
                            'topology', 'schedule', 'limitations') if key in bundle})
            # C8 preserves its separate-process evidence envelope. Flatten only
            # the reader view; the downloadable signed evidence is untouched.
            if bundle['case'] == 'C8':
                result['branches'] = {name: {**branch, 'label': branch.get('name', name),
                    'checkpoints': [{**frame, **frame['observed'], 'id': frame['name'],
                        'outstanding_promises': sum(life.get('source') == 'A issuance'
                                                    for life in frame['observed']['lifecycles'])}
                        if 'observed' in frame else frame for frame in branch['checkpoints']]}
                    for name, branch in bundle['branches'].items()}
            result['accounting_window'] = bundle.get('accounting_window', bundle.get('units', {}).get('accounting_window'))
        if metadata.get('error'): result['error'] = metadata['error']
        return result

    def catalog(self):
        cases = [{'id': case, 'title': item['title'],
                  'implementation_status': 'implemented/unpinned',
                  'supported': not (self.demo and case == 'C8'),
                  'support': ('Local operator only; hosted process execution is unsupported.'
                              if self.demo and case == 'C8' else
                              'Fixed bounded schedule in this private workspace.' if self.demo else
                              'Local synthetic fixture; no production rail integration.')}
                 for case, item in CASES.items()]
        # Listing does not load every full signed history into memory.
        runs = [{'id': folder.name, 'case': metadata['case'],
                 'title': CASES[metadata['case']]['title'], 'status': metadata['status']}
                for folder in sorted(self.directory.glob('coverage_*'), key=lambda p: p.stat().st_mtime, reverse=True)
                if (folder / 'request.json').exists()
                for metadata in [_read(folder / 'request.json')]]
        return {'cases': cases, 'runs': runs}

    def run(self, case, request_id):
        require(case in CASES, 'COVERAGE_CASE', 'Unknown coverage schedule.')
        if self.demo and case == 'C8':
            from .public_demo import refuse
            refuse('COVERAGE_LOCAL_ONLY',
                   'C8 requires the local operator’s separate-process fixture. Hosted visitor execution is unsupported.', 403)
        # The existing workspace lock serializes creation and request-id lookup.
        # Retrying an ambiguous HTTP response cannot create another experiment.
        with self.experiments.lock:
            for record in self.directory.glob('coverage_*/request.json'):
                existing = _read(record)
                if existing['request_id'] == request_id:
                    require(existing['case'] == case, 'IDEMPOTENCY_CONFLICT',
                            'This request identifier already selected another schedule.')
                    return self.view(record.parent.name)
            cost = CASES[case]['books']
            if self.demo and self.experiments.book_count() + cost > self.demo.manager.limits.books:
                from .public_demo import refuse
                refuse('DEMO_BOOK_LIMIT', 'This coverage schedule exceeds the workspace’s remaining experiment book quota.')
            lease = self.demo.heavy() if self.demo else nullcontext()
            with lease:
                identifier = uid('coverage')
                folder = self.directory / identifier
                folder.mkdir(mode=0o700)
                metadata = {'case': case, 'book_cost': cost, 'request_id': request_id, 'status': 'running'}
                _write(folder / 'request.json', metadata)
                try:
                    from .coverage_experiments import run_case
                    bundle = run_case(case, folder / 'schedule')
                    _write(folder / 'observed.json', bundle)
                except Exception:
                    metadata.update(status='failed', error='The schedule failed. No completed observation is claimed; partial local evidence is retained.')
                    _write(folder / 'request.json', metadata)
                    raise
                metadata['status'] = 'observed'
                _write(folder / 'request.json', metadata)
                return self.view(identifier)

    def export(self, identifier):
        folder = self.folder(identifier)
        require((folder / 'observed.json').exists(), 'COVERAGE_PENDING',
                'No completed observed run is available for export.')
        return _read(folder / 'observed.json')


def register(app, operator, store_dependency, prefix='/api/operator/coverage'):
    def select_store(experiments=Depends(store_dependency)):
        return CoverageStore(experiments)

    @app.get(prefix, dependencies=[Depends(operator)])
    def catalog(store=Depends(select_store)):
        return store.catalog()

    @app.post(prefix+'/{case}/run', dependencies=[Depends(operator)])
    def run(case: Literal['C8', 'C9', 'C10'], request: RunCoverage,
            store=Depends(select_store)):
        return store.run(case, request.request_id)

    @app.get(prefix+'/runs/{identifier}', dependencies=[Depends(operator)])
    def view(identifier: str, store=Depends(select_store)):
        return store.view(identifier)

    @app.get(prefix+'/runs/{identifier}/export', dependencies=[Depends(operator)])
    def export(identifier: str, store=Depends(select_store)):
        with store.demo.heavy(budget=False) if store.demo else nullcontext():
            return store.export(identifier)
