# A missing receipt does not create capacity

Open **Missing receipt experiment** from the dashboard. Choose **Run experiment**
for the complete execution, or follow the six steps. Both policies reserve
atomically. A deadline starts reconciliation; it does not prove that an order
never executed. The timeout policy is an intentionally incorrect experimental
control, not a competing product or a benchmark against other protocols.

The demonstration uses synthetic buy orders. Acceptance creates a working order;
it is not a fill. An expired capability refuses a new redemption, but expiry
does not cancel an order that was already accepted.

## Local reproduction

From a fresh checkout with Python 3.11+, Node 20.19+ (22 recommended), npm and Make:

```sh
make reproduce-uncertain
```

This installs the project's locked dependencies and Chromium, executes both
policies, verifies the evidence, runs the complete backend and browser suites,
and builds the dashboard. Existing contained-process tests also need Linux,
Bubblewrap and `/usr/bin/python3`; see the main README for their setup. Missing
Chromium system libraries can be installed with
`cd frontend && npx playwright install --with-deps chromium`.

Results go to `.runtime/uncertain-evidence/`. Prior evidence is never overwritten.
The directory also contains `uncertain-evidence.zip` and a SHA-256 manifest of
the public artifacts for copying or archival.
To choose a fresh directory, or to reproduce only the experiment without rerunning
the test suites:

```sh
.venv/bin/python scripts/reproduce_uncertain.py --checks all --output .runtime/uncertain-run-2
.venv/bin/python scripts/reproduce_uncertain.py --checks none --output .runtime/uncertain-scenario-only
.venv/bin/python -m saac.uncertain_verifier .runtime/uncertain-evidence/evidence.json
```

`--checks none` records that tests were not run. It must not be cited as a full
conformance run. `--checks backend` runs the backend suite and is the script's
default. Use `make demo` to explore the same implementation interactively.

## Fixed schedule and measurements

One displayed capacity unit equals **100 integer `notional_usd_cents`**. Each buy
order has quantity 1 and limit price 100 cents. The ceiling is 1,000 cents, or
10 units. The fixture uses no portfolio netting, model API, market prices, broker,
payment account, private data or arbitrary subprocess execution.

Each policy has its own identically configured book, issuer and socket keys.
The clock starts at Unix time 1,800,000,000. The recorded schedule determines
which original admitted orders receive fills; the identity of admission winners
can vary because 100 actual threads contend after a shared barrier. This is a
concurrency test, not evidence of distributed deployment or a throughput result.

| Step | Virtual offset | Expected evidence-based book U / Q / available | Expected timeout book U / Q / available | Rail obligations, evidence / timeout |
|---|---:|---|---|---|
| Initial admission | 0 | 0 / 10 / 0 | 0 / 10 / 0 | 0 / 0; each also has 10 valid promises |
| Working orders and hidden fills | 1 | 0 / 10 / 0 | 0 / 10 / 0 | 10 / 10 |
| Reconciliation deadline | 61 | 0 / 10 / 0 | 0 / 0 / 10 | 10 / 10 |
| Fresh second batch | 62 | 0 / 10 / 0 | 0 / 10 / 0 | 10 / 20 |
| Recovered original fills | 63 | 4 / 6 / 0 | 0 / 10 / 0, late fill evidence rejected | 10 / 20 |
| Confirmed original cancellations | 64 | 4 / 0 / 6 | 0 / 10 / 0, four consumed units remain unaccounted | 4 / 14 |

Both initial batches admit exactly 10 requests and deny 90. Execution is held
until that measurement completes. Four original orders then fill fully; six
remain working. The accepted working-order receipts keep U=0 and Q=10. Fill
receipts exist in the socket journal but are withheld from the authority.

The second batch contains ten **new request identifiers**, not retries.
The evidence-based path admits zero; the timeout path admits and redeems ten.
Recovery delivers genuine signed cumulative receipts. Six separately authorized
cancellations resolve the six original remaining commitments. Only the correct
book regains six usable units through accepted terminal evidence.

The CSV and comparison table in the generated bundle contain actual observed
values, not this expected-results table. Every dashboard number comes from the
backend. Selecting an earlier frame is labeled **Replay**, and does not execute
new work. Reset creates a new comparison and preserves the old evidence within
the workspace's lifetime and resource limits.

## Independent observation

At every frame the authority reports `available = L - U - Q` in display units.
The experiment observer independently replays durable order-acceptance, fill and
cancellation events:

```text
rail obligation = cumulative executed notional + remaining live order commitment
live order commitment = unfilled quantity × admitted limit price
total modeled commitment = rail obligation + valid outstanding unredeemed promises
```

A redeemed capability is replaced by its order in this measurement; the two are
never added together. The initial admission frame therefore shows zero rail
obligation and ten outstanding promises. After the second batch has been
redeemed, outstanding promises are zero and rail obligations are 10 versus 20.
The book may report zero available and still omit ten units of real fixture
obligation after an invalid timeout release. Raw differences are retained;
negative values must not be hidden by clipping.

This observer is a **fixture oracle**, not an independent external auditor.
Its observations are not signed institutional receipts. It cannot feed hidden
fills into reconciliation before the evidence-delivery phase. Exports distinguish
raw oracle order records, signed journal receipts, accepted evidence, and invalid
experimental accounting events.

## Reconciliation and the deliberately invalid transition

The experiment configures a `reconcile_by` deadline. Reaching it records an
investigation-due event. The evidence-based workflow queries the accepted socket
journal through the controlled evidence-availability boundary, records an
investigation, and escalates unknown outcomes to an operator exception queue.
Each exception identifies the reservation, age, reason and next action. The
deadline bounds investigation/escalation, not availability of an external answer.
Unknown commitments remain reserved.

For non-use, the existing protected socket closes the unused nonce and creates
its signed non-use receipt under the same transaction lock as redemption. Either
closure or execution wins; capacity is released only after accepted evidence.
Already working orders instead require separate cancellation authority. Cumulative
receipt revisions are signed, journal-bound and idempotent; duplicates and older
revisions cannot release capacity a second time.

The timeout control makes one small, explicit deviation: experiment-only code
changes the ten original allocations to released without terminal evidence and
records each invalid transition. Its ordinary allocator and protected socket are
otherwise unchanged. It uses real atomic reservations, exact-effect capabilities,
signatures and single-use redemptions for both batches. No ordinary actor or
production reconciler can choose this policy.

The normal reconciler refuses late fill evidence against an allocation already
released by this invalid transition. The control preserves the signed evidence,
rejection and observed obligation. Cancellation of original working orders does
not erase the four executed units. The unsafe run must fail ordinary tape
conformance; experiment-verifier success means the intended breach was detected.

## Isolation, durability and evidence

Only institutional operator endpoints and the existing isolated public demo
workspace expose the fixture. Actor credentials cannot change its policy,
deadline, clock, fills, observer or reservations. Each comparison consumes two
books from the existing public quota and uses the existing heavy-job limiter.
The public host remains single-process; durable locks and checkpoints serialize
step/run/replay operations. Distinct visitors cannot address each other's books.
Session expiry removes temporary data, so download evidence before ending it.

Restart tests reconstruct service objects using the same on-disk books, keys,
nonce and socket journal. This is **service reconstruction**, not a new OS process,
machine failure, distributed failover or a remote exchange recovery test. Signed
artifacts use local fixture keys; the event hash chain is not externally anchored.

The evidence bundle contains:

- Source commit, clean/dirty status, source-tree digest, runtime/dependency and OS
  versions, frozen clock and schedule.
- Both policies' chronological events, admission records, public verification
  keys, signed capabilities and receipts, book allocations and durable rail records.
- Stage frames, expected/observed verifier checks, comparison table and figure CSV.
- Actual suite commands/results and test identifiers, plus desktop/mobile screenshots
  when the full browser run is requested. CI URL is null unless a CI run was verified.

The verifier checks signatures, event chains, capability/receipt bindings,
book transitions, phase admission outcomes and independently reconstructed order
obligations against dashboard/export frames. It detects altered evidence and
separately reports normal tape conformance. It trusts the local fixture's keys,
journal completeness and recorded scenario; it cannot prove a remote institution
executed these effects or detect a fully replaced unanchored history.

Private keys, credentials, runtime databases and provider secrets are excluded.
Use a clean executable-code commit for final evidence. If code changes during a
reproduction, the command fails its source-unchanged check. A later commit storing
generated evidence is distinct from the source commit recorded as executed.

The implementation follows the existing Docker/Render build. Local success does
not establish a hosted deployment. The paper and OpenCode integration are outside
this change.
