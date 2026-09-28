# Validation and benchmarks

Run the checks from the repository root:

```bash
make test
make test-ui
```

For the uncertainty experiment, `make reproduce-uncertain` executes the scenario,
checks the evidence independently, and records both suites with test identifiers,
runtime/source provenance, figure data and actual desktop/mobile screenshots.
See [the uncertainty guide](UNCERTAIN_EXECUTION.md). An unsafe-control breach is
an expected experimental finding, never normal conformance success.

For v3.9 C8–C10 use `make reproduce-coverage`; see the
[coverage schedules guide](COVERAGE_SCHEDULES.md) for case selection and the exact
evidence layout. The process-restart, parent-revocation, and shared-allocation
tests evaluate observed states against expected-only checkpoint data. The unsafe
branches retain ordinary conformance failure and have a separate intended-breach
detection result. No historical baseline test count is a target for a new run.

`tests/test_coverage_api.py` checks actor/operator separation, strict fixed-schedule
inputs, request-id recovery, visitor isolation, shared quotas and retained failed
attempts using an explicitly labeled transport stub. It is not schedule evidence.
`frontend/tests/coverage.spec.ts` and the coverage helper reused by the existing
public-session test execute real backend schedules and compare
displayed observations with the actual API response, check separate predicates
and replay state, and capture desktop/mobile UI evidence. C8 is exercised only
in the local operator profile; visitor tests assert its explicit refusal and
run bounded C9/C10. Reusing that existing visitor preserves every coverage
assertion while keeping the full suite within the unchanged session-start quota.
Hosted provider deployment remains a separate check.

This repository starts a new Git history; earlier source pins, signed evidence
archives and CI records are not published evidence for this checkout. See the
[C8–C10 evidence notice](research/coverage-evidence/README.md) and
[uncertain-execution evidence notice](research/uncertain-evidence/README.md).
Report actual counts and source provenance from a new run, including skipped or
unsupported checks. Expected checkpoint data never substitutes for observations.

The backend suite exercises exact-effect binding, tampering, expiry, audience,
replay, live reservations, delegation narrowing, concurrent admission, durable
retries, signed receipts and reconciliation across service restarts. The browser
suite exercises the visible scenarios, visitor isolation, refresh recovery,
desktop and phone layouts. `make test-ui` also builds the production frontend.

Concurrency tests use real threads and barriers. One payment scenario releases
100 distinct agents at once, each requesting $100,000 against a shared $1M book.
Exactly ten capabilities fit. Swarm tests also exercise 1,200 and 5,000 logical
actors, checking the aggregate book and replaying its signed evidence.

Contained-process tests require Linux, Bubblewrap and system Python. They run
fixed programs with supervisor-bound request channels. Missing containment
fails closed; these tests do not authorize arbitrary host code.

## Synthetic workload benchmarks

The [retained raw benchmark data](research/swarm-end-to-end-2026-09-17.json)
contains historical observations dated 17 September 2026. It records environment
metadata but no executed source commit or source-tree digest. It is therefore
unpinned historical data, not evidence for the current implementation or its
throughput. No new performance result is claimed by retaining that file.

To record a new run without replacing the retained raw data:

```bash
.venv/bin/python docs/research/benchmark_swarm.py
```

Results are written under `.runtime/benchmarks/`, which is excluded from Git.
Record the executed source commit, clean/dirty status, source-tree digest and
actual command result alongside any benchmark offered as current evidence.
Logical actors are persisted identities, not model calls or thousands of OS
processes. Browser polling and host load affect wall time; a single synthetic
observation is not a production throughput guarantee.
See the [threat model](THREAT_MODEL.md) for assumptions and limits on the claims.
