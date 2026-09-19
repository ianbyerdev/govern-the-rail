# SAAC executable fixture specification

This document describes the implementation of Shared Agent Authority Contracts
in **Govern The Rail Lab**, accompanying Ian Byer's *Govern the Rail, Not the
Brain: Shared Agent Authority Contracts for Concurrent AI Systems*.

Wire snapshots use `saac_version: "0.3.0"`. Wire, software and policy-pack
versions are independent implementation identifiers.

## Contract and scope

Proposal is not authority. A policy verdict is not authority. Authority is not
unreserved capacity. The protected socket independently verifies a signed κ for
one resolved effect. Signed socket evidence ρ, not an actor report, moves risk.
This is a local reference fixture with synthetic payments, trading, referrals,
contained jobs and agent workflows. It is not a production payment system or a
claim of SAAC certification. No external consequential endpoint is contacted. Unknown operations, including raw shell/spawn/dispatch, fail closed.

## Actors, duties and boundaries

| Component | Responsibility | Authority |
|---|---|---|
| Actor / mock proposer | Propose, retry, retain a diagnostic risk snapshot | Actor API credential only; cannot administer R |
| Institutional operator | Publish policy, review exact snapshots, reconcile, halt | Separate operator credential; explicitly trusted demo control surface |
| Resolver Rres | Canonicalize aliases and bind resource/route/reference versions | Risk-book-owned tables |
| Pack evaluator | Deterministic allow/deny/modify/escalate/degrade | Returns a verdict, never a redeemable permission |
| Authority issuer | Recheck authoritative state, reserve, sign | Risk-book key and serialized transaction |
| Protected socket | Verify signature, bindings and live state; consume nonce; execute | Socket identity and independent receipt key |
| Receipt consumer | Validate evidence and settle idempotently | Risk-book transaction |
| Tape | Preserve proposal, snapshot, decision inputs, authority and results | Monotonic, hash-chained records |

R = {P, Rres, G, U, Q, N, K} follows equation (10). R owns pack history and
live pointer, resolver aliases/versions, grants and parent chains, settled usage,
reservations, nonces/redemptions, approvals, keys and breaker state. The actor
owns only proposals, task context and **non-authoritative** observed counters.
Actor credentials must not reach operator APIs. The dashboard is an operator's
inspection/control surface, not an actor sandbox. Local development process/API
separation does not claim protection from a malicious process running as the
same OS user; an optional container boundary removes database/key mounts from
the actor. Risk book and socket are separate components within one trusted local
service, with separate signing keys; they share a transaction domain for the toy
rail. Core code has no FastAPI dependency.

## Resolved effect and Wire

Use equation (1) verbatim: **e = ⟨p, s, o, r, x, c⟩**.

- p: represented principal, fixed by authenticated actor identity/grant.
- s: protected socket/audience (`PAYMENTS-1`).
- o: typed effect (`payment` or a mediated, scoped `dispatch`).
- r: canonical beneficiary resource ID (or parent grant for scoped dispatch).
- x: canonical arguments: integer `amount_cents`, currency, route, purpose;
  dispatch contains the entire child scope and identity. No floating-point money.
- c: resource version, route version, sanctions/reference version, grant version,
  pack hash and tool/schema commitment. No actor-owned state establishes c.

Canonical encoding is a documented sorted-key compact UTF-8 JSON subset with no
floats/NaN; SHA-256 is prefixed `sha256:`. Aliases use Unicode NFKC, trim and
casefold, then look up an authoritative table. Two names for one beneficiary
produce the same r and e; rebinding a name resolves to a different e. The socket
re-resolves the submitted call immediately before execution. Identity, scope,
schema, destination, versions, time, nonce and snapshot hash are signed. Missing
or unknown fields fail schema validation.

A snapshot includes: ID, monotonic sequence, ts/exp, nonce,
agent/harness/informational model, principal/grant/parent chain, audience,
tool/schema, declared/resolved class, canonical arguments, destination/resource,
pack ID/hash, observed risk, breaker, provenance, host completeness commitment,
and e. Actor observations are retained as evidence, never used for admission.

## Policy and atomic issuance

An institution-signed versioned pack defines tool catalog, canonical resolver
state, allowed beneficiaries/currencies/routes/purposes, per-action/session
ceilings, approval threshold, maximum TTL, attenuation and breaker behavior.
Grant scope is an independent upper bound. Unknown resources, principal mismatch,
expired/revoked grants, unsupported operations and bad versions deny.

The pure evaluator records all inputs needed to replay P, including the pack,
grant, authoritative U/Q/L, committed time and approval evidence. Verdicts are
allow, deny, modify, escalate and degrade. Modify can only reduce amount with
identical resource, route, purpose and identity; recompute b(e). It cannot exceed
Auth(G). Breaker degradation is an authoritative grant rewrite to no payment/
dispatch authority, with a version increment. Only independent operator action
can restore authority.

For this scalar rail b(e) = amount_cents (zero for scoped dispatch).
`BEGIN IMMEDIATE` serializes issuance. Re-read live pack, grant, resolver,
breaker and **U + Q + b(e) ≤ L** inside that transaction. Persist reservation
`⟨H(e), b(e), reserved, exp, book_seq⟩`, nonce, signed capability, trace and
approval consumption together. Return κ only **after commit**. Signing or commit
failure returns no authority and leaves no partial reservation. A response lost
after commit may strand a reservation; it may not create capacity.

## κ lifecycle and socket rule

The capability binds: `typ`, `kid`, `aud`, `exp`, `nonce`,
`grant_id`, `pack_hash`, `effect_digest`, `reservation.{id,book_seq,bound}`, `sig`.
Also bind principal, agent, issued-at, snapshot hash/ID, exact effect and delegation
chain. The encoding is a local Ed25519 signed JSON realization of the profile,
not a new production token standard. Public verification keys are inspectable;
private keys never go to the actor or dashboard.

The socket independently checks: trusted signature/type/key; principal/
agent; its own audience; iat ≤ now < exp; unused nonce and committed reservation;
live pack hash; exact effect digest and snapshot; live resolver/state; active
grant and precheck. Canonicalization, state check, nonce consumption, simulated
effect and durable socket receipt are in one transaction so policy updates and
redemption cannot race the effect. A verdict-shaped object is rejected.
An unverified/tampered invocation never frees the original reservation. Replay
cannot cause a second payment or free additional capacity.

## ρ and conservative reconciliation

Retain `typ`, `snapshot_hash`, `kappa_id`, `reservation_id`, `result`,
`settlement.{consumed,released,reservation_status}`, `ts`, `sig`; include receipt
ID, socket signer/audience, nonce, execution ID, monotonically increasing receipt
revision and cumulative consumed/released/remaining amounts. Integer cumulative
totals make out-of-order evidence safe. Every receipt binds to the stored κ and
reservation and is signed by the accepted socket key.

Reservation states: reserved → executing/uncertain → partial → settled/released.
Full execution moves Q to U. Partial execution moves only the executed amount to
U, retaining the live remainder in Q. A later authoritative cancel releases only
the remainder. Terminal rail rejection releases the full unused bound. The receipt
consumer validates signatures, bindings, conservation, monotonic cumulative totals
and revisions in a transaction. Duplicate/older receipts have no financial effect.
Conflicting receipts with equal revision are rejected. Terminal state cannot reopen.

Lost/delayed receipt: socket ledger records the effect, but R retains the full
unreconciled bound in Q until accepted evidence arrives. Expiry prevents redemption;
**time alone never releases Q**. Reconciliation reads durable socket evidence or
atomically closes an unused nonce and produces signed non-use/cancellation proof.
Crash before execution can strand Q; crash in the toy socket transaction rolls
back both effect and nonce; crash after execution before delivery retains evidence.
This local atomic simulation does not claim atomicity with a remote bank.

## Human approval

Escalate persists an immutable, expiring exact snapshot, with no reservation or κ.
The only actionable approval surface displays principal, audience, amount,
canonical beneficiary, currency, route, resource/pack versions, purpose and expiry.
Approval/rejection submits snapshot ID and hash. Approval is single-use and
cannot override scope or collars. On approval, recheck current state and capacity;
changed policy/resource state requires a new snapshot. A modified call cannot
inherit approval because κ and the approval bind the original effect.

## Delegation

Equation (3): Auth(child) ⊆ Auth(parent), exp(child) ≤ exp(parent). Enforce subset
resources/routes/currencies/purposes/operations, smaller ceilings, same principal,
same session collar and bounded depth. Cross-principal transfer requires a fresh
institutional grant and is denied by delegation. A scoped dispatch is itself an
exact-effect mediated operation with a zero-capacity reservation and signed ρ;
its execution creates a child grant, not an arbitrary OS process. Revoked/degraded
ancestors invalidate descendants. No alternate credentials or child session can
manufacture capacity.

## Pack liveness and failure behavior

Only the active signed hash is live. Policy publication has increasing version,
attributable change event, prior hash and effective time. Superseded hashes remain
for replay but fail socket verification. Rollback to a prior version/hash is denied.
Lowering L beneath U+Q is refused; an operator can halt then reconcile instead.
Unavailable authority state, invalid signatures, missing fields and unknown
outcomes fail closed. Attack lab controls are operator-only and clearly separated
from the actor credential. Unsafe check-then-act comparison uses isolated memory,
never the actual issuer, socket or database.

## Falsifiable properties and acceptance tests

| Invariant | Mechanical evidence |
|---|---|
| 1 Complete mediation | Unsigned/verdict-only, unsupported operation, raw spawn rejected; payments and scoped dispatch require κ |
| 2 Separation | Actor token cannot read private pack/key/admin state or change limits/resolver/grants/breaker; forged observed risk ignored |
| 3 Replay and settlement | Replay deterministic decisions; verify tape chain, κ/ρ; duplicate/reordered receipts idempotent; partial/cancel; loss/restart/non-use |
| 4 Limit integrity | Modify-up denied, approved amount mutation denied, no actor-created relief; 100 simultaneous one-unit requests vs ten-unit L yield exactly ten grants |
| 5 Binding integrity | Amount/resource/principal/audience/nonce/expiry/schema/version tampering; concurrent redemption; stale pack and rollback |
| 6 Delegation monotonicity | Narrow child executes; every widening dimension, expiry, principal transfer, ancestor revoke fails |
| 7 Canonicalization integrity | Equivalent aliases same e; alias rebound/different beneficiary fails; authoritative resolution only |

Concurrency stress also delays, drops, duplicates and reorders signed receipts,
partially executes and cancels. At every committed transition, verify U ≥ 0,
Q ≥ 0 and U + Q ≤ L. Mechanically compare the isolated intentionally unsafe race.

## Domain profiles

Payments, trading, referrals and contained jobs use the same authority, nonce
and receipt pipeline. Every operator-created experiment has its own persisted book,
keys, profile and fixed session; actor credentials cannot create or select these
books. Changing the visible domain does not reset an existing book.

- Trading uses `OMS-17`, bounded synthetic buy limit orders, integer cents and
  integer shares. Admission reserves `notional_usd_cents`. Submission creates a
  working order; accepted fill evidence moves actual notional to U. A separately
  authorized cancellation releases only the unfilled remainder. The simulated EMS
  is owned exclusively by the protected OMS and has no actor endpoint.
- Referrals use `REFERRAL-EGRESS-1`. A sealed synthetic record manifest binds
  patient, recipient, purpose, content hashes, byte lengths and consent/directory
  versions. Admission reserves both `records` and `bytes`. Release to the local
  recipient inbox is irreversible usage; future consent revocation cannot undo it.
- Runtime uses `RUNTIME-1`. Only an institution-owned fixed script and constrained
  job specification may launch. `job_starts` is cumulative; `job_slots` is held
  until accepted exit evidence. Bubblewrap restricts mounts, credentials, process
  namespace and network. Unsupported containment fails closed; no host-shell
  fallback exists. Durable intent and a claim prevent automatic duplicate launch;
  ambiguous launch status retains reservations. Trusted runner evidence drives ρ.

The common additive invariant is checked for every named dimension in one SQLite
write transaction. Receipts conserve each original allocation across cumulative
consumed, released and remaining values, with monotonic evidence revisions.
Occupancy is represented by remaining allocation, released on accepted exit;
starts remain consumed. All profile/schema IDs, units, effect arguments and state
commitments are included in the signed effect/capability. Cross-profile/audience
capabilities and receipts fail closed. Unknown profile operations are denied.

Live UI controls trigger actual transitions. Historical events are inspection,
never undo. The risk lane distinguishes unaccepted socket evidence from accepted
reconciliation. Unsigned/scope/OS refusals and an isolated unprotected dummy sink
are labelled with the mechanism actually responsible for the observed outcome.
