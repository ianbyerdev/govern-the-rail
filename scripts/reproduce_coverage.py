#!/usr/bin/env python3
"""Execute C8–C10 and export public evidence, verification and actual checks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from saac.coverage_experiments import CASES
from saac.uncertain_provenance import provenance
from reproduce_uncertain import backend_results, browser_results, dump, run_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('all', *CASES), default='all')
    parser.add_argument('--checks', choices=('none', 'backend', 'all'), default='backend')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = (args.output or ROOT / '.runtime' / 'coverage-evidence' / stamp).resolve()
    # Reproduction is append-only at directory granularity, including failed runs.
    if output.exists():
        parser.error('Output already exists. Choose a fresh --output; prior evidence is never overwritten.')
    output.mkdir(parents=True)
    source = provenance()
    cases = CASES if args.case == 'all' else (args.case,)
    reports, checks, artifacts = {}, [], []
    for case in cases:
        print(f'Executing {case}…', flush=True)
        command = [sys.executable, '-m', 'saac.coverage_experiments', '--case', case,
                   '--output', str(output / f'{case}.json')]
        checks.append(run_check(case, command, output))
        if not (output / f'{case}.json').exists():
            reports[case] = {'valid': False, 'negative_control_detected': False,
                             'errors': ['Schedule process failed before exporting evidence; see '+case+'.log.']}
            artifacts.append(case+'.log')
            continue
        bundle = json.loads((output / f'{case}.json').read_text())
        reports[case] = bundle['verification']
        artifacts += [f'{case}.json', f'{case}.log']
    if args.checks != 'none':
        checks.append(run_check('backend', [sys.executable, '-m', 'pytest', '-q',
                               '--junitxml=' + str(output / 'backend.xml')], output))
        artifacts += ['backend.log', 'backend.xml']
    if args.checks == 'all':
        checks.append(run_check('build', ['npm', 'run', 'build'], output, cwd=ROOT / 'frontend'))
        environment = {**os.environ, 'PLAYWRIGHT_JSON_OUTPUT_NAME': str(output / 'browser.json'),
                       'SAAC_EVIDENCE_DIR': str(output)}
        checks.append(run_check('browser', ['npm', 'test', '--', '--reporter=json'], output,
                                cwd=ROOT / 'frontend', env=environment))
        artifacts += ['build.log', 'browser.log', 'browser.json', 'coverage-c9-final.png',
                      'coverage-c10-final.png', 'coverage-c8-second-batch.png', 'coverage-c9-visitor-mobile.png',
                      'coverage-c10-visitor-mobile.png',
                      'uncertainty-desktop.png', 'uncertainty-mobile.png',
                      'uncertainty-checkpoint-desktop.png']
    end_source = provenance()
    backend = backend_results(output / 'backend.xml')
    browser = browser_results(output / 'browser.json')
    results = {'checks': checks, 'backend': backend, 'browser': browser,
               'backend_counts': {status: sum(t['result'] == status for t in backend)
                                  for status in ('passed', 'failed', 'skipped')},
               'browser_counts': {status: sum(t['result'] == status for t in browser)
                                  for status in ('expected', 'unexpected', 'flaky', 'skipped')},
               'scope': args.checks, 'ci_run_url': None,
               'source_unchanged_during_run': source['source_tree_sha256'] == end_source['source_tree_sha256'],
               'invocation': [sys.executable, *sys.argv],
               'unavailable': ['Hosted deployment/browser execution was not performed by this command.'],
               'skipped': (['Backend regression suite', 'Frontend build', 'Browser suite'] if args.checks == 'none'
                           else ['Frontend build', 'Browser suite'] if args.checks == 'backend' else [])}
    dump(output / 'test-results.json', results)
    dump(output / 'verification.json', reports)
    dump(output / 'source.json', source)
    artifacts += ['test-results.json', 'verification.json', 'source.json']
    rows = ['# Observed C8–C10 run', '', f"Executed source: `{source['source_commit']}`.",
            source['revision_claim'], '', 'All domain numbers in case JSON are integer cents; display scale is 100.',
            'Unsafe branches fail ordinary conformance. Experiment verification passes only when the specified breach is detected.', '',
            '| Case | Evidence valid | Intended breach detected |', '|---|---|---|']
    rows += [f"| {case} | {report['valid']} | {report['negative_control_detected']} |" for case, report in reports.items()]
    rows += ['', f"Backend counts: `{results['backend_counts']}`.",
             f"Browser counts: `{results['browser_counts']}`.",
             'See test-results.json for commands, exit codes and explicit skipped checks.',
             'No private keys, credentials, live databases or claimed CI URL are included.',
             'The full fixture history and public keys are locally rooted and not externally anchored.']
    (output / 'README.md').write_text('\n'.join(rows) + '\n')
    artifacts.append('README.md')
    files = [output / name for name in artifacts if (output / name).is_file()]
    dump(output / 'manifest.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    with zipfile.ZipFile(output / 'coverage-evidence.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for file in [*files, output / 'manifest.json']:
            archive.write(file, arcname=file.name)
    success = all(r['valid'] for r in reports.values()) and all(c['exit_code'] == 0 for c in checks) and results['source_unchanged_during_run']
    print(json.dumps({'output': str(output), 'success': success, 'source': source['source_commit'],
                     'source_dirty': source['source_dirty'], 'cases': reports,
                     'backend_counts': results['backend_counts'], 'browser_counts': results['browser_counts']}, indent=2))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
