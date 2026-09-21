"""Fixed C8–C10 operator schedules; no user-supplied executable or command."""
from pathlib import Path

from .uncertain_provenance import provenance

CASES = ('C8', 'C9', 'C10')


def run_case(case, directory):
    """Execute a fresh schedule and attach verification, never expected output."""
    if case not in CASES:
        raise ValueError('Unknown coverage schedule')
    from .coverage_c8 import run_c8
    from .coverage_c9 import run_c9
    from .coverage_c10 import run_c10
    from .coverage_verifier import verify_bundle
    source = provenance()
    result = {'C8': run_c8, 'C9': run_c9, 'C10': run_c10}[case](Path(directory))
    result['units'] = {**result['units'], 'name': 'integer cents', 'display_name': 'synthetic units'}
    result['provenance'] = source
    result['status'] = ('observed_pinned_local_run' if source['source_dirty'] is False
                        else 'implemented_unpinned_observed_run')
    result['source_unchanged_during_run'] = source['source_tree_sha256'] == provenance()['source_tree_sha256']
    result['verification'] = verify_bundle(result)
    return result


def main():
    import argparse
    import json
    import tempfile
    parser = argparse.ArgumentParser(description='Execute one fixed coverage schedule and write public evidence.')
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; choose a fresh evidence path.')
    with tempfile.TemporaryDirectory(prefix='risc-coverage-') as state:
        result = run_case(args.case, Path(state) / 'schedule')
    with args.output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps({'case': args.case, 'verification': result['verification']}, indent=2))
    return 0 if result['verification']['valid'] and result['source_unchanged_during_run'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
