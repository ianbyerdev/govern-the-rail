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

## Recorded local benchmark

These measurements were recorded on 17 September 2026 using Python 3.14.4 on
Linux x86_64 and 16 bounded scheduler workers. The
[raw measurements](research/swarm-end-to-end-2026-09-17.json) include environment
metadata and risk-book results.

| Workload | Logical actors | Protected executions | Denials | Run time | Tape replay |
|---|---:|---:|---:|---:|---:|
| Fan-out | 100 | 64 | 36 | 1.905 s | 0.045 s |
| Fan-out | 1,200 | 64 | 1,136 | 18.184 s | 0.278 s |
| Fan-out | 5,000 | 64 | 4,936 | 67.157 s | 1.361 s |
| Useful team | 1,000 | 564 | 436 | 20.322 s | 0.830 s |

These are single observations of synthetic workloads, not production throughput
guarantees. Logical actors are persisted identities, not model calls or thousands
of OS processes. Signatures, reservations, local effects and receipts are real.
Browser polling and host load affect wall time.

To record a new run without replacing the published measurements:

```bash
.venv/bin/python docs/research/benchmark_swarm.py
```

Results are written under `.runtime/benchmarks/`, which is excluded from Git.
See the [threat model](THREAT_MODEL.md) for assumptions and limits on the claims.
