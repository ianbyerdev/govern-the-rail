# Incident / Swarm Lab: five-minute walkthrough

Run `make demo` and select **Incident / Swarm Lab**. Everything uses local synthetic
resources. The existing payment, OMS/EMS, referral and script workspaces remain.

1. Choose **A useful research team**, 2 actors, full coverage. Create and Play.
   Select actor 0: inspect the proposal, resolved effect, real signed κ, signed ρ
   and trace. Actor 1 starts a narrow child. The child slot stays held.
2. Choose **The delegation storm**, 100 actors, **Concurrent stress**. Create and
   Play. Exactly 64 children can acquire slots; the remaining 36 proposals are
   denied by the shared book. Increase the population to 1,200 or 5,000 to see the
   same collar across thousands of distinct identities. Those are logical actors;
   the interface reports bounded scheduler workers and zero model calls separately.
3. Choose **The missing receipt**, 2 actors. Play, then **Return missing receipts**.
   Uncertainty clears, lifetime starts become committed, and the two running slots
   remain held. **Authorize stops** produces separate exit evidence and releases
   only slots. Starts never come back.
4. Choose **The uncovered side door**, partial coverage. Both the protected refusal
   and the purple oracle-only effects are visible. Select full coverage above and
   **Compare with full coverage**, then Play. The exact intent manifest is reused
   in a fresh independent book; the uncovered effects disappear.
5. Explore **It is only a simulation**, **A found bearer credential**, **The shared-cache
   bridge**, **A capability at the wrong door**, **A parent asks for more authority**,
   and **Revoke between issue and execution**. Inspect the plain-language reasons
   and exact failed bindings. **Valid authority, bad policy** intentionally succeeds.

Play runs in the background; Pause stops after the current batch. Step processes
one intent through the full lifecycle. Closing the browser does not erase a run.
After a server restart, Resume/Play recovers committed intents without executing
them twice. Existing books are available in the experiment selector.

Expand source context to see which reported facts inspired the scenario and what
was invented. The Anthropic scope-confusion preset repeats an isolated-instance
pattern across a synthetic population; it does not claim those incidents coordinated.
Use reactive mode with the shared-cache story: a denied first message prevents the
next modeled read proposal. Fixed replay instead attempts every predetermined intent.
Communication via a namespace is different from issuing a child grant.

The real-worker panel launches 2 fixed Bubblewrap processes through authorized
spawn effects. It shows actual mount/network probes and supervisor-bound request
results. The Python API permits 2–8 workers. No host fallback exists if containment
is unavailable. This small process proof is separate from the large logical population.

Institution controls can publish a new pack, halt authority, restore supervision,
stop children or close unused capabilities. Halting does not undo effects; expired
capabilities and missing receipts do not automatically restore capacity. Restore
supervision before issuing stops. Old child grants do not inherit restored authority.

**Verify & export tape** produces public keys, signed artifacts, replayed policy
verdicts, the manifest, source notes and separately labeled oracle observations.
The verifier checks each admission against reconstructed U + Q and checks receipt
conservation. No private signing key or actor/operator credential is exported.

Reproduce measurements:

```bash
PYTHONPATH=. .venv/bin/python docs/research/benchmark_swarm.py
.venv/bin/pytest tests/test_swarm.py -q
```

The 5,000-actor stress fixture retains thousands of signed audit objects and can
take roughly a minute on a local machine. This is an auditable research simulation,
not a throughput claim. Prefer 100 actors for a short live presentation.

For a slower boundary walkthrough, create the useful-team experiment and select
actor 0. **Stage selected κ** reserves capacity without executing. **Modify effect**
and **Wrong socket** fail; **Present original κ** succeeds; **Attempt replay** fails.
A fresh staged action can demonstrate **Change resource version** or **Advance past
expiry**. **Close unused κ** requires signed non-use evidence before capacity returns.
Campaign authority time is virtual and preserved across comparison clones; the wall
clock measurement reports actual execution cost separately.
