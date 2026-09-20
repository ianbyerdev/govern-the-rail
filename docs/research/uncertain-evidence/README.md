# Recorded uncertain-execution evidence

Executable source: `d71558e5df1623445ee54c83538e5f44cffd3d2d`, published on `main`.
This was a clean checkout and the source fingerprint remained unchanged throughout
execution and testing. This directory is stored in a later evidence-only commit;
that storage commit is not the revision claimed as executed.

The actual command was:

```sh
.venv/bin/python scripts/reproduce_uncertain.py --checks all --output .runtime/uncertain-verified-evidence
```

All 271 backend tests and 36 browser tests passed; the production frontend build
passed. There were two existing dependency deprecation warnings and no skipped,
failed or retried tests in this full reproduction.

The same source revision passed [GitHub CI](https://github.com/ianbyerdev/govern-the-rail/actions/runs/35510591652)
with 271 backend and 36 browser tests; [ci-result.json](ci-result.json) records the
run identifier and successful conclusion. It was deployed to
[Render](https://govern-the-rail.onrender.com/) in
[deployment dep-dant2q2jnfac739p2ab0](https://dashboard.render.com/web/srv-danfc33tqb8s73bnugkg/deploys/dep-dant2q2jnfac739p2ab0).
The [hosted smoke report](release-smoke.json) records a fresh public visitor run,
independent verification of its exported evidence, the expected deployed revision,
10-versus-20 second-batch obligations, and six available units after reconciliation.
The smoke test ended its own temporary visitor session.

Release validation found and fixed an exact-label browser-test race: the Scenario
selector's wrapping label included its dynamically populated option text. The test
now selects the combobox by its accessible role and name. This archive contains
the successful full reproduction after that fix. The earlier failed reproduction
is retained locally in `.runtime/uncertain-release-evidence/`.

Both policies initially admitted 10 of 100 actors. The fresh second batch admitted
zero versus ten requests, and durable rail records yielded 10 versus 20 units of
obligation. Correct reconciliation recovered U=4/Q=6, then U=4/Q=0 with six units
available after authorized cancellation. The timeout control ended with 14 units
of obligation and four consumed units unaccounted by its authority book.

- [Complete evidence archive](uncertain-evidence.zip), including signed artifacts,
  public keys, event journals, exact test identifiers/results, runtime versions,
  source metadata, screenshots and SHA-256 manifest. No private keys or credentials.
- [Actual comparison table](comparison.md) and [reproducible figure data](figure-data.csv).
- [Independent verifier report](verification.json). Its success means the intended
  unsafe breach was detected. The timeout control fails ordinary conformance.
- [Desktop final state](uncertainty-desktop.png),
  [desktop second-batch replay](uncertainty-checkpoint-desktop.png), and
  [mobile final state](uncertainty-mobile.png), captured from the actual browser tests.

To inspect the archive and verify it independently:

```sh
unzip docs/research/uncertain-evidence/uncertain-evidence.zip -d .runtime/archived-uncertainty
.venv/bin/python -m saac.uncertain_verifier .runtime/archived-uncertainty/evidence.json
```

For a fresh full reproduction, follow [the experiment guide](../../UNCERTAIN_EXECUTION.md).
The default output directory preserves prior results; choose a fresh `--output`
when repeating the run.

Limits: synthetic local order rails, a fixture oracle and unanchored local event
chains. Recovery was tested through service reconstruction over existing SQLite
journals, not termination of an OS process, distributed failover, or a real broker.
No paper changes or OpenCode integration are included.
