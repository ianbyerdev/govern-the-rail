#!/usr/bin/env python3
"""Execute the fixture, verify evidence, and optionally run the actual suites."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from saac.uncertain import UncertainExperiment
from saac.uncertain_provenance import provenance
from saac.uncertain_verifier import verify_bundle


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def run_check(name, command, output, *, cwd=ROOT, env=None):
    print(f'Running {name}…', flush=True)
    with (output / (name + '.log')).open('w') as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    return {'name': name, 'command': command, 'exit_code': result.returncode,
            'result': 'passed' if result.returncode == 0 else 'failed', 'log': name + '.log'}


def backend_results(path):
    if not path.exists(): return []
    def nodeid(case):
        parts = case.attrib['classname'].split('.')
        for end in range(len(parts), 0, -1):
            module = '/'.join(parts[:end]) + '.py'
            if (ROOT / module).exists():
                return '::'.join([module, *parts[end:], case.attrib['name']])
        return case.attrib['classname'] + '::' + case.attrib['name']
    return [{'id': nodeid(case),
             'result': 'failed' if case.find('failure') is not None or case.find('error') is not None
                       else 'skipped' if case.find('skipped') is not None else 'passed',
             'seconds': case.attrib.get('time')}
            for case in ET.parse(path).iter('testcase')]


def browser_results(path):
    if not path.exists(): return []
    report = json.loads(path.read_text())
    def walk(suites, prefix=''):
        for suite in suites:
            title = (prefix+' › '+suite['title']).strip(' ›')
            for spec in suite.get('specs', []):
                for test in spec.get('tests', []):
                    yield {'id': title+' › '+spec['title'], 'file': spec.get('file'),
                           'line': spec.get('line'), 'result': test.get('status'),
                           'attempts': [r.get('status') for r in test.get('results', [])]}
            yield from walk(suite.get('suites', []), title)
    return list(walk(report.get('suites', [])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'.runtime/uncertain-evidence')
    parser.add_argument('--checks', choices=('none', 'backend', 'all'), default='backend')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output/'evidence.json').exists():
        parser.error('Output already contains evidence. Choose a fresh --output to preserve prior results.')
    source = provenance()
    with tempfile.TemporaryDirectory(prefix='saac-uncertain-') as state:
        experiment = UncertainExperiment(Path(state))
        experiment.run()
        evidence = experiment.export()
    evidence['provenance'] = source
    verification = verify_bundle(evidence)
    evidence['verification'] = verification
    checks = []
    if args.checks != 'none':
        checks.append(run_check('backend', [sys.executable, '-m', 'pytest', '-q',
                                          '--junitxml='+str(output/'backend.xml')], output))
    if args.checks == 'all':
        checks.append(run_check('build', ['npm', 'run', 'build'], output, cwd=ROOT/'frontend'))
        environment = {**os.environ, 'PLAYWRIGHT_JSON_OUTPUT_NAME': str(output/'browser.json'),
                       'SAAC_EVIDENCE_DIR': str(output)}
        checks.append(run_check('browser', ['npm', 'test', '--', '--reporter=json'], output,
                                cwd=ROOT/'frontend', env=environment))
    end_source = provenance()
    tests = {'checks': checks, 'backend': backend_results(output/'backend.xml'),
             'browser': browser_results(output/'browser.json'),
             'source_unchanged_during_run': source['source_tree_sha256'] == end_source['source_tree_sha256'],
             'ci_run_url': None, 'scope': args.checks}
    evidence['test_results'] = tests
    dump(output/'evidence.json', evidence)
    dump(output/'verification.json', verification)
    dump(output/'test-results.json', tests)
    rows = []
    for frame in evidence['frames']:
        for policy, panel in frame['policies'].items():
            rows.append({'stage': frame['stage'], 'policy': policy, 'virtual_time': frame['now'],
                         **panel['book'], **{key: panel['observer'][key] for key in
                         ('executed_units', 'working_units', 'obligation_units', 'outstanding_promises',
                          'total_commitment_units', 'breach_units', 'unaccounted_units')}})
    with (output/'figure-data.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# Observed comparison', '', f"Executable source: `{source['source_commit']}`.",
             source['revision_claim'], '', '| Phase | Policy | U | Q | Available | Rail obligation |',
             '|---|---|---:|---:|---:|---:|']
    for row in rows:
        lines.append(f"| {row['stage']} | {row['policy']} | {row['consumed']} | {row['reserved']} | {row['available']} | {row['obligation_units']} |")
    lines.extend(['', 'The timeout policy is a deliberately invalid experimental control.',
                  'Evidence verifier success means its breach was detected, not that it passed normal conformance.',
                  'See evidence.json for signed artifacts, raw rail observations, source/runtime metadata and test identifiers.',
                  'Private keys, credentials and temporary databases are excluded.'])
    (output/'comparison.md').write_text('\n'.join(lines)+'\n')
    # Archive only the explicit public artifacts written by this workflow. Never
    # sweep a user-selected output directory for arbitrary existing files.
    names = ['evidence.json', 'verification.json', 'test-results.json', 'figure-data.csv',
             'comparison.md', 'backend.xml', 'backend.log', 'build.log', 'browser.json', 'browser.log',
             'uncertainty-desktop.png', 'uncertainty-mobile.png', 'uncertainty-checkpoint-desktop.png']
    files = [output/name for name in names if (output/name).is_file()]
    dump(output/'manifest.json', {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
    with zipfile.ZipFile(output/'uncertain-evidence.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in [*files, output/'manifest.json']:
            archive.write(path, arcname=path.name)
    success = verification['valid'] and tests['source_unchanged_during_run'] and all(c['exit_code'] == 0 for c in checks)
    print(json.dumps({'output': str(output), 'valid': verification['valid'],
                      'negative_control_detected': verification['negative_control_detected'],
                      'checks': checks, 'success': success}, indent=2))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
