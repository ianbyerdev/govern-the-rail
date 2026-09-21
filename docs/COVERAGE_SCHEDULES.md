# Agentic RISC v3.9 coverage schedules

Agentic RISC separates independent institutional controls from the actor so that
agents can choose actions without choosing their own limits. Its Action Contract
links effect, decision, allocation, executable capability and accepted outcome.
The RISC Runtime owns the institutional authority book; the RISC Gateway enforces
the exact effect at the protected rail. The failure schedules substantiate this
architecture; they are not a measure of useful throughput or model quality.

## Inspection and source-to-requirement map

Implementation began on the clean `f32c4e5` checkout of `main`, after fetching
`origin`. The difference from historical source `d71558e` was a later, preserved
commit storing uncertainty evidence. Initial collection found 271 backend tests;
this is a collection count, not a new passing result. Existing `.runtime` stores
and credentials were left untouched. No lab server was running. The repository
uses Python/FastAPI, React/Vite, SQLite, Ed25519 artifacts, pytest and Playwright.
It has no Sites hosting configuration.

The supplied v3.9 TeX, including Appendices A–D, and the implementation brief are
the specification. The PDF and accompanying expected-checkpoint JSON were not
attached. `docs/research/coverage/expected-checkpoints.json` is an explicitly
expected-only transcription of the supplied tables, not a recovered attachment
and not measured evidence.

| Requirement | Existing source | Implementation / verification |
|---|---|---|
| Reserve before authority; exact effect and audience | `authority.py`, `protected_socket.py`, `protocol.py` | Reused signed types and normal reconciliation |
| C8 independent persistence and process restart | Existing profile co-locates book and rail | `remote_rail.py`, `coverage_c8.py`, `test_remote_rail.py` |
| C9 revoke future authority, retain commitments | `service.py`, grant lineage checks, `reconciliation.py` | `coverage_c9.py`, `test_parent_revoke.py` |
| C10 true shared scope and conserved alternatives | Per-book integer admission | `coverage_c10.py`, `test_two_books_one_prime.py` |
| E includes promises without double counting | Uncertainty fixture issuance/rail observer | `coverage_common.py`, independent `coverage_verifier.py` |
| Observed state, isolated dashboard | `uncertain_api.py`, `public_demo.py`, React lab | `coverage_api.py`, `CoverageLab.tsx`, browser/API checks |
| Reproducible evidence, explicit unsafe failures | `uncertain_provenance.py`, existing reproduction conventions | `reproduce_coverage.py`, verifier mutation tests |

Paths in the map are implementation locations; a source location alone is not
evidence that a check passed. The reproduction bundle records actual commands,
exit codes and observations separately.

## Names and compatibility

Reader-facing labels use Agentic RISC, Action Contract, RISC Runtime, RISC Gateway
and Actor environment. The existing `saac` Python package, `saac-*` signed types,
key identifiers and wire fields remain compatible. Historical tapes and their
signatures are not renamed. An adapter carries authority; it owns neither a
signing key nor a competing allocation.

## Evidence boundary

All new schedules use synthetic buy notional in integer cents, at 100 cents per
displayed unit, in one declared fixed accounting window. They evaluate ledger
compliance (`U + Q <= L`), coverage (`E <= U + Q`) and, for C10, the true shared
ceiling (`E <= L_star`) separately. Negative headroom and coverage gaps remain
visible. A parent covering allocation and its suballocations cover the same
lifecycles and are never summed as new obligations.

The evaluator can read issuance and rail history. It is read-only and does not
deliver hidden outcomes to the Runtime. Only the explicit accepted-evidence
interface can justify a release. Rail-only evidence is sufficient only when
the checkpoint has no outstanding promises outside the rail journal. Both the
domain and promise count are exported.

Evidence is locally signed fixture history, not an independently anchored
institutional attestation. Signature, binding, event continuity and reconstructed
snapshot checks detect mutations within a bundle. Replacing the complete
unanchored history, its public keys and all matching artifacts is outside these
guarantees. Each Runtime and rail also signs a fixture checkpoint over its full
public snapshot and history head. These extra artifacts authenticate captured
history and projections; their distinct signed types are never accepted by the
normal receipt interface as authority to release capacity. No production
certification, host-compromise resistance or universal
tool mediation is claimed.

## Reproduce and inspect

From the repository root, run all schedules and the supported regression checks:

```sh
make reproduce-coverage
```

The command installs the repository dependencies and Chromium, actually executes
all three schedules in fresh temporary stores, then runs the full backend suite,
frontend build and browser suite. It creates a new timestamped directory beneath
`.runtime/coverage-evidence/`; no prior evidence is overwritten. An explicit output
directory must not already exist. Live runtime databases, credentials and private
keys are excluded from the exports and archive.

Individual schedules, with only scenario execution and verification:

```sh
.venv/bin/python scripts/reproduce_coverage.py --case C8 --checks none
.venv/bin/python scripts/reproduce_coverage.py --case C9 --checks none
.venv/bin/python scripts/reproduce_coverage.py --case C10 --checks none
```

`--checks backend` adds the complete backend regression suite; `--checks all` also
builds the frontend and runs Playwright. A scenario-only run explicitly records
these regression checks as skipped. A failed verifier or subprocess gives a
nonzero command exit. The expected JSON is compared only after independent
reconstruction of the observed run; it is never used to populate the dashboard.

Offline verification of an exported case:

```sh
.venv/bin/python -m saac.coverage_verifier .runtime/coverage-evidence/RUN/C8.json
```

Each bundle includes `C8.json`, `C9.json`, and/or `C10.json`, verification results,
source commit/dirty status/tree digest, environment versions, explicit units and
window, ordered/frozen schedule, topology, admissions, public keys, signed
capabilities/receipts, accepted and rejected evidence, raw rail records, captured
Runtime snapshots, oracle reconstruction and expected-versus-observed checks.
`test-results.json` records actual subprocess commands and exit codes. Backend
JUnit and browser JSON give exact observed counts; UI screenshots are included
when browser checks run. A SHA-256 manifest covers the allowlisted archive files.
No CI URL is inferred from a source commit; a verified CI result is stored
separately from the local run.

## Schedule semantics

[C8's protocol description](REMOTE_RAIL_C8.md) states the distinct A claim and B
acceptance linearization points, separate keys/stores/processes, retained charge
on ambiguous dispatch, and explicit evidence-delivery interface. Local operator
support is exercised separately from the public visitor profile. Hosted C8 runs
are disabled, and a local run does not establish hosted support.

C9 gives the competitor and cleanup actor separate active root grants outside
the revoked subtree, drawing on the same ten-unit allocation. A recorded
counterfactual with zero occupancy confirms the competitor passes non-capacity
policy checks; actual admission uses the real occupied book and denies specifically
with `SESSION_LIMIT`. The child can no longer start work, while ordinary accepted
fill and cancellation evidence settles its historical commitment. The mandatory
unsafe branch retains signed late evidence and normal `RECEIPT_REGRESSION` or
`RECEIPT_TERMINAL` rejection. No repair rule erases the historical failure.

C10 uses one typed downstream account with `L_star=1000` cents. Three independent
configurations use separate local stores. In the unsafe configuration the
downstream Gateway accepts correctly bound local authority without a common
allocation; each local book remains green while the shared scope fails. In the
covering configuration a Runtime-owned SQLite transaction acquires the shared
hold before calling local issuance. A acquires the ten-unit hold first in the
presentation; a separate two-thread contention test permits either winner.
The split assigns six and four units and checks conservation both in assignment
and subsequent local admission. The cover and suballocations are views of the
same lifecycles, not additive obligations.

Failed local issuance or an ambiguous response leaves its covering hold charged.
A retry with the same durable request and intent recovers the original result;
a different intent conflicts. Only a definite durable local denial with no
capability can release an unissued hold. C10 does not implement release of cover
after accepted execution. A downstream acceptance surviving local rollback is
recovered by the same capability lifecycle; the local receipt binds the signed
downstream acceptance hash and original downstream execution identity. This is
a bounded recovery test, not distributed exactly-once execution.

The dashboard renders backend records with explicit replay controls, source
status and separate conformance/detection indicators. Its local books, aggregate
scope, promises, negative headroom and coverage gaps remain visible. Public C9
and C10 use the existing visitor workspace, quota and heavy-job boundaries;
the fixed controls accept no executable, shell command or arbitrary filesystem
path. There is no additional actor-controlled service role.

## Next measurement, not a result

The useful-work experiment should compare evidence-based recovery with a safe
hold-indefinitely policy under the same issuance, execution, delay, failure and
retry schedules. Both reserve before issuance and refuse unsupported release.
The conservative comparator keeps unresolved allocations even after an outcome
could be recovered; the recovery branch queries the declared evidence interface,
validates cumulative outcomes, and releases only proven non-use or cancellation.
Report useful completions, stranded capacity, exception age and recovery time,
with the unsafe timeout control separately identified. No availability or
throughput measurements are claimed by C8–C10.
