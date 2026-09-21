# C8: Independent rail survives a Runtime restart

C8 uses the existing signed capability and receipt formats, ordinary Authority,
and ordinary Reconciler. The fixed synthetic order Gateway lives in a separate
operating-system process. Its SQLite journal B has a separate writer, database,
transaction and rail signing key. Runtime process A owns the institutional book,
grants, policy, issuer key and allocation. No Authority object or database
connection is passed between the processes. Only public verification keys and
bounded JSON messages cross the private Unix-domain transport.

Independent controls let the Actor environment choose actions within the
institution's mandate. The restart schedule demonstrates one reason the Runtime
must preserve that mandate when outcomes are uncertain: the protected rail can
retain accepted commitments while Runtime has received none of their receipts.

## Redemption and non-use protocol

1. The Gateway verifies the issuer signature, audience, authenticated fixture
   identity and exact proposed order. Its B write transaction serializes all
   attempts for this lifecycle, including non-use closure. An existing B result
   returns its original execution and receipt without executing again.
2. Before new acceptance, B asks A to claim the committed reservation. A checks
   the live policy, resource resolution, grant lineage, breaker, expiry, exact
   snapshot and every bound dimension under its own write transaction. A writes
   a durable nonce claim and deterministic execution identity before answering.
3. This claim is the authority-use decision: the allocation stays charged, and
   the exact effect is frozen for the in-flight execution. Later grant revocation
   cannot erase an existing claim. The B transaction then commits the simulated
   order, durable single-use nonce, acceptance event and signed receipt together.
   B's commit is the protected acceptance linearization point. No transaction
   spans the two stores.
4. Non-use closure takes the same B lock and asks A to change an unused nonce to
   closed. It then durably records and signs B's non-use result. It may release
   A's allocation only after that signed result is explicitly delivered and
   accepted. Redemption and non-use cannot both win.

If A is unavailable, eligibility cannot be established and new acceptance fails
closed. If A commits its claim but B has not committed, no non-use proof is
available: the allocation remains charged. A retry uses the same execution
identity and must still pass live eligibility before B accepts it. If new
eligibility fails after that interruption, this limited profile retains the
allocation indefinitely; it does not invent a release protocol. If B already
committed, durable retry returns the existing result even while A is down. A
changed request cannot use that retry path to execute a different effect.

This protocol is limited to this one local Gateway and its durable SQLite
serialization boundary. The test does not establish distributed exactly-once
execution, failover among independent B stores, network-partition availability,
or protection against malicious code running as the trusted host user.

## Accepted evidence versus evaluator observations

The evaluator joins A's immutable issuance events with B's acceptance/fill/non-use
events by capability lifecycle. Before redemption, ten valid promises contribute
ten units even though B has no accepted order. After redemption, each promise is
replaced by its execution lifecycle. The evaluator also retains unresolved
dispatch claims if no B outcome is present. It never derives E from reservation
status labels.

These read-only observations are not institutional receipts. The original
acceptance receipts stay exclusively in B through the required checkpoints.
The explicit remote receipt inbox verifies the B signature and lifecycle binding,
retains the delivered artifact in A's evidence journal, and invokes the unchanged
Reconciler. That reconciler applies ordinary cumulative, idempotent settlement.
The recovery stage delivers a later one-unit fill: safe A changes from U=0/Q=10
to U=1/Q=9. Duplicates and older receipt revisions do not apply again. The unsafe
branch retains the signed late receipt and the ordinary reconciler's rejection
after its unsupported release.

## Fixed experiment and exported evidence

Both branches issue ten one-unit order capabilities under a ten-unit ceiling and
durably accept them in B while withholding outcome delivery to A. The controller
uses SIGKILL to terminate the actual Runtime process, proves B's PID and journal
survive, restarts A from its own store, and retries the original request and
redemption identity without a duplicate order. It then submits ten new request
identifiers. The safe branch denies them for headroom. The isolated unsafe
control clears the original allocation without evidence and accepts the second
batch, preserving both its ordinary-conformance failure and the detection result.

The schedule uses integer notional cents, with 100 cents per displayed synthetic
unit, inside one fixed accounting window. Exports preserve negative modeled
headroom and positive coverage gaps. All reported checkpoints come from snapshots
of the executing processes, with raw events, signed capabilities and receipts,
public keys, admissions, process history and the reconstructed lifecycle set.
Each process signs its own snapshot digest, event-chain head and process/store
identity as a `coverage-book-checkpoint` fixture attestation. These attestations
detect edits to retained snapshots; they are not accepted outcome receipts and
the Runtime's evidence inbox refuses them as authority for accounting changes.
Their keys remain local fixture trust roots, not an independent external anchor.

`run_c8(output_directory)` in `saac.coverage_c8` runs the schedule and writes
`observed.json`. Existing evidence is never overwritten. Live databases and
private keys are held in a temporary local state directory and removed when the
schedule exits. Test commands and source provenance belong to the enclosing
reproduction bundle. A replaced unanchored fixture history remains outside the
verifier's guarantees.

The process endpoints offer only fixed synthetic commands. They do not execute
arbitrary programs or user-selected shell commands. Public and hosted execution
of C8 is unsupported and must remain separately labelled; the local operator
schedule is not evidence that a hosting platform supports this topology.
