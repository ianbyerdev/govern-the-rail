# Observed Agentic RISC v3.9 C8–C10 evidence

Executed source: `f7d27db01702f61f1dca0fd12f59900ca030f8da` on branch `implementation/risc-v3.9-coverage`.
The checkout was clean and its source-tree digest remained unchanged throughout
the final run: `2cf1134745cd125d2f542897133c1ac51c03ffd761e7050bcfdc421b846381f1`.
This directory is stored in a later artifact commit, not the executed source.
The source and artifacts are currently local: branch publication failed because
Git HTTPS push credentials were unavailable. No new CI URL is claimed.

## Observed validation

| Check | Actual result |
|---|---|
| C8, C9, C10 execution and offline verifier | All three passed; all unsafe branches retain ordinary conformance failure |
| Full backend regression | 359 passed, 0 failed, 0 skipped |
| Frontend production build | Passed |
| Complete browser suite | 39 passed, 0 failed, 0 skipped |
| Local Docker public-profile smoke | 43/43 checks passed; 23 HTTP requests |

Backend output includes two existing Starlette/httpx deprecation warnings.
The new browser fixture reuses an existing visitor session so it stays inside the
unchanged start quota. All original coverage assertions remain. The earlier
clean-source run's real browser failure is retained in `earlier-run-4f5e3dc.zip`
and `earlier-browser-failure.zip`, rather than overwritten.

## Requirement-to-source/test map

| Requirement | Source | Tests |
|---|---|---|
| C8: separate A/B processes and stores; actual kill/restart, durable retries, normal evidence recovery | [remote_rail.py](../../../saac/remote_rail.py), [coverage_c8.py](../../../saac/coverage_c8.py) | [test_remote_rail.py](../../../tests/test_remote_rail.py) |
| C9: revoked lineage stops; independently valid competitor denied for headroom; retained child occupancy and late rejection | [coverage_c9.py](../../../saac/coverage_c9.py) | [test_parent_revoke.py](../../../tests/test_parent_revoke.py) |
| C10: true shared ceiling, reservation before issuance, conserved split and contention/retry recovery | [coverage_c10.py](../../../saac/coverage_c10.py) | [test_two_books_one_prime.py](../../../tests/test_two_books_one_prime.py) |
| Signed evidence reconstruction and tampering rejection | [coverage_verifier.py](../../../saac/coverage_verifier.py) | [test_coverage_verifier.py](../../../tests/test_coverage_verifier.py), C8 mutation tests |
| Isolated real-state dashboard and controls | [CoverageLab.tsx](../../../frontend/src/CoverageLab.tsx), [coverage_api.py](../../../saac/coverage_api.py) | Coverage API, coverage browser, public visitor browser and container checks |

The [coverage guide](../../COVERAGE_SCHEDULES.md) explains the protocols, adapter
boundary, accepted evidence versus evaluator, public quotas and future useful-work
experiment. Original package names, signed formats and historical tapes remain
compatible. Expected values are transcribed specification targets; all numbers
below come from the executed backend, reconstructed before comparison.

## Commands and evidence

Actual full run:

```sh
.venv/bin/python scripts/reproduce_coverage.py --checks all --output .runtime/coverage-pinned-f7d27db
```

All six subprocess checks (three schedules, backend, build, browser) exited zero.
`test-results.json` retains exact argv, individual test results and exit codes.
`coverage-evidence.zip` contains the case bundles, keys, signatures, raw execution
history, snapshots, source/environment provenance, verifier results, logs, JUnit,
browser JSON and screenshots. Its internal SHA-256 manifest covers these files.
Private keys, credentials and live databases are excluded.

New full reproductions use `make reproduce-coverage` or the same script with a
fresh output directory. Individual commands are:

```sh
.venv/bin/python scripts/reproduce_coverage.py --case C8 --checks none
.venv/bin/python scripts/reproduce_coverage.py --case C9 --checks none
.venv/bin/python scripts/reproduce_coverage.py --case C10 --checks none
```

The individual commands explicitly skip the broad regression suites. Offline
verification uses `python -m saac.coverage_verifier C8.json` (or C9/C10) after
extracting the bundle in a checkout of the executed source.

`container-evidence.zip` binds a separate Docker build and public API smoke to
the same clean source. C9/C10 exported evidence verified both on the host and
inside the image. Packaged Git provenance correctly remains unavailable; the
external build manifest binds its image ID to the source. The container was
removed after testing.

## Observed accounting checkpoints

Displayed synthetic units below use exactly 100 integer notional cents per unit.
The bundle gives the fixed accounting window and clock/schedule. C10 rows are
independent configurations; its L and available capacity use true L_star=10.
Local C10 A/B books and covering rights are in the raw snapshots and dashboard.

| Case | Branch/configuration | Checkpoint | U | Q | E | Available |
|---|---|---|---:|---:|---:|---:|
| C8 | safe | promises_issued | 0 | 10 | 10 | 0 |
| C8 | safe | accepted_receipts_withheld | 0 | 10 | 10 | 0 |
| C8 | safe | runtime_restarted | 0 | 10 | 10 | 0 |
| C8 | safe | unsupported_release | 0 | 10 | 10 | 0 |
| C8 | safe | second_batch | 0 | 10 | 10 | 0 |
| C8 | safe | accepted_outcome_recovery | 1 | 9 | 10 | 0 |
| C8 | unsafe | promises_issued | 0 | 10 | 10 | 0 |
| C8 | unsafe | accepted_receipts_withheld | 0 | 10 | 10 | 0 |
| C8 | unsafe | runtime_restarted | 0 | 10 | 10 | 0 |
| C8 | unsafe | unsupported_release | 0 | 0 | 10 | 10 |
| C8 | unsafe | second_batch | 0 | 10 | 20 | 0 |
| C8 | unsafe | accepted_outcome_recovery | 0 | 10 | 20 | 0 |
| C9 | safe | child_accepted | 0 | 6 | 6 | 4 |
| C9 | safe | parent_revoked | 0 | 6 | 6 | 4 |
| C9 | safe | competitor | 0 | 6 | 6 | 4 |
| C9 | safe | child_completed | 2 | 0 | 2 | 8 |
| C9 | unsafe | child_accepted | 0 | 6 | 6 | 4 |
| C9 | unsafe | parent_revoked | 0 | 0 | 6 | 10 |
| C9 | unsafe | competitor | 0 | 6 | 12 | 4 |
| C9 | unsafe | child_completed | 0 | 6 | 8 | 4 |
| C10 | unsafe | promises | 0 | 20 | 20 | -10 |
| C10 | unsafe | accepted | 0 | 20 | 20 | -10 |
| C10 | covering | promises | 0 | 10 | 10 | 0 |
| C10 | covering | accepted | 0 | 10 | 10 | 0 |
| C10 | split | promises | 0 | 10 | 10 | 0 |
| C10 | split | accepted | 0 | 10 | 10 | 0 |

## Limitations and manuscript boundary

These are synthetic order/account fixtures. C8 demonstrates separate processes
and persistence on one trusted host, without distributed exactly-once or host
compromise resistance. Public C8 execution remains disabled. Local visitor and
Docker profiles were tested; a remote hosting provider was not deployed or tested.
There is no production OMS/payment/EHR integration, universal tool mediation,
model alignment claim, or measured availability benefit.

The signed fixture checkpoints are not accepted institutional receipts and are
not externally anchored. Complete replacement of the fixture history and its
keys remains outside verifier guarantees. C9 retains the mandatory late-rejection
branch; no optional repair or useful-work measurement is claimed.

Only the v3.9 TeX was attached. The matching PDF and accompanying expected JSON
were unavailable; the expected-data transcription is explicitly labeled in source.
`Agentic_RISC_v3.9_proposed_coverage.patch` narrowly promotes the implemented,
pinned local schedules while preserving the practitioner-led structure. It is a
proposal, not an applied edit to the supplied manuscript. Its application was
checked against the exact attached TeX; PDF compilation was not performed.
