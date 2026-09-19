# Threat model

The fixture assumes an untrusted proposer and trustworthy institutional controls,
not a safe model. Security claims apply to the simulated protected rail.

Public visitors have isolated, expiring synthetic workspaces. Their session
cookie is not the host operator credential. Server-side store selection prevents
cross-visitor access; public routes cannot launch the real runtime or worker
processes. Quotas and bounded jobs constrain the anonymous workload, while a
production proxy must bound connections and request duration. The visitor plays
the institution within their own experiment, so policy editing there is an
intentional teaching control, not access to someone else's authority. See
[PUBLIC_DEMO.md](PUBLIC_DEMO.md) for the exact boundary and remaining hosting work.

## Adversary capabilities

An actor may alter proposals and observed risk, reuse aliases, submit unsupported
operations, tamper with signed objects, present authority to another audience,
race concurrent issuance/redemption, seek enlarged child scope, retry after denial,
or claim an effect that did not occur. The lab additionally lets the trusted
operator simulate state changes, delayed/lost receipts and partial execution.

## Trusted computing base

- Institutional identity/API credential checks and operator approval surface.
- Resolver and its versions/reference data; canonicalization cannot repair lying
  source data. Resource changes must update their authoritative commitments.
- Pack store/live pointer, grant/ancestor store, risk ledger and transaction engine.
- Risk-book signer, socket verifier, socket receipt signer and reconciliation code.
- Local simulated rail transaction. It cannot secretly contact another endpoint.
- Host OS/container runtime. In local mode, the current OS user is trusted.

## Attacks and enforcement

| Attack | Enforcement | Evidence |
|---|---|---|
| Fake low actor counters | Issuer computes U/Q from independent ledger | Forged snapshot test |
| Bypass mediation / submit verdict | Socket requires valid κ and committed reservation | Unsigned/verdict/unknown operation tests |
| Modify-up / beneficiary / currency / purpose / route change | Re-resolved exact e must match signed H(e) | Parameterized substitution tests |
| Stolen/replayed capability | Bound identity/audience, short validity, atomic nonce consumption | Wrong audience and 20-thread redemption race |
| Expiry / stale resource | Clock and c checked at redemption | Expiry and TOCTOU tests |
| Policy rollback | Only current signed pack hash is live; versions increase | Supersession and rollback tests |
| Stale human approval | Immutable exact snapshot, expiry, recheck at issuance | Approval mutation/capacity tests |
| Concurrent capacity theft | U+Q+b ≤ L inside `BEGIN IMMEDIATE` | 100-thread shared-collar fixture |
| Phantom relief / missing receipts | Q retained until accepted evidence | Lost receipt, restart and non-use tests |
| Duplicate/reordered partial/cancel receipts | Signed cumulative revisions and monotonic settlement | Reordered/duplicate evidence tests |
| Delegation laundering | Subset all dimensions; same principal/session; ancestor liveness | Child/widening/revocation tests |
| Tape editing | Hash chain, signature validation, deterministic replay | Tamper and retained-head test |

A local bearer operator credential is enough to change policy because this is an
operator demonstration, not enterprise authentication. It must never be handed to
the actor. The API does not enable cross-origin access, and administrative requests
require an Authorization header rather than automatically attached cookies.
The launcher binds only loopback; Compose exposes only a loopback port. No
private key is served, and the actor API never returns the stored pack or tape.

## Explicit non-claims

This fixture does not protect a host from malicious same-user Python code. Use
the separate container to demonstrate lack of actor filesystem access; it still
does not prove a hardened sandbox against kernel escape. The shared trusted
service does not protect the risk book against a compromised socket implementation.

This fixture does not solve off-rail actions, model-generated dangerous knowledge,
trajectory composition, operator fatigue, bad policy, compromised institutional
keys/resolver, production authentication, availability attacks, time synchronization,
real-bank exactly-once semantics, or external audit anchoring. These limitations define the scope of the implementation.

Fail-closed behavior can strand capacity. That is an intentional availability
tradeoff, visible in the UI; it is never repaired by deleting reservations or
resetting counters automatically.

## Multi-domain and contained-runtime extension

Additional trusted components are the domain schemas, manifest sealer, synthetic
recipient inbox, local OMS-owned EMS, fixed-script supervisor, Bubblewrap, host
runtime files and signed exit journal. The host runtime is a trusted read-only
mount, not a fully hermetic image of every system library. The script and selected
executable identities are hashed and checked; the sealed per-job script is what
the child executes. Kernel exploits and hostile institution/host administrators
remain outside the claim.

Job intents are not unrestricted shell capabilities. No user-authored script,
argv, host mount, environment variable or external network destination is accepted.
Only the fixed probe script can run. On platforms without containment the job is
refused; the service never falls back to direct host execution. A launch claim
without exit evidence retains capacity, including across service restart.

The deliberately unprotected sink is an explicit counterexample: an ordinary
actor credential can write a fixed synthetic payload to a separate dummy database
without κ. It cannot write the protected payments, orders, inbox, ledger or keys.
The coverage lab reports which mechanism actually acted: socket signature,
monotonic grant checks, OS mount/network containment, or no enforcement. A refusal
at one gate is not proof of complete mediation for all possible agent tools.

The browser is still an operator console. Creating isolated experiment books and
simulating EMS fills are operator-only test controls, never actor abilities.
Runtime child scope cannot change the represented principal, widen permissions,
increase ceilings, outlive its parent or obtain an independent session collar.

## Incident counterfactuals

The modeled adversary can make arbitrary strict proposals, lie in transcript/risk
snapshots, possess a fixed mock bearer credential, follow peer instructions,
change an invocation, and try replay or another audience. Protected paths require
live exact authority independently. No real leaked key, exploit payload, arbitrary
host path, arbitrary program, hostname or network destination is accepted.

The full-coverage posture is an explicit assumption about modeled paths, not an
inventory of every path on a real platform. Partial coverage writes synthetic bytes
to a separate oracle table without κ. Those observations never become institutional
receipts or reduce reservations. The reachability-only posture intentionally has
no SAAC accounting at all. Neither posture contacts outside services.

Bad-policy mode deliberately authorizes public-role publishing. This distinguishes
correct enforcement from a wise policy. Policy, resolver, signer, registry, deputy,
supervisor, Python/Bubblewrap binaries, host libraries and kernel remain trusted.
A compromised trusted component, unknown vulnerabilities, arbitrary covert channels,
real DNS/TLS rebinding and hostile operators who own unenforced rails are outside
this fixture's prevention claim.

The actual worker proof is narrower than sandboxing arbitrary malicious software:
2–8 fixed benign Python programs probe isolation, then emit three typed requests
on private stdout pipes. The supervisor supplies channel identity. The probes
show OS restrictions; request rejection shows SAAC restrictions. Neither is proof
against a kernel exploit. Missing launch outcome strands risk conservatively.

Historical source notes distinguish observations, publication dates, event periods
and invented simulation parameters. Anthropic assessment incidents are isolated
instances, not coordinated-agent incidents. The 1,200-agent preset does not claim
1,200 simultaneous spawned descendants. No historical prevention percentage is shown.
