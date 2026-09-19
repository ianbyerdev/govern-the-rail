# Five-minute demonstration

Run `make demo`. The dashboard is the **institution operator**: its mock proposer
cannot access the operator credential, policy, keys or reservation ledger. All
business resources are synthetic. The runtime executes one fixed script inside
Bubblewrap on Linux; it contacts no external service.

The three columns show: **Actor book → Risk book → Protected
socket**. The receipt arrow returns to the risk book. The selected experiment
identifier and current live pack stay visible. **Fresh experiment** creates a
separate book; the selector retains earlier experiment books. Changing a domain reopens its latest experiment, not a new risk allowance.

## 0:00 — A proposal, a verdict, then authority

In Payments, click **Resolve & evaluate**. The resolver binds the canonical
beneficiary, arguments and state, and the pack records a verdict. κ is still
absent and reserved capacity is zero. Click **Request authority**: $75 becomes Q
before any payment exists. The server recomputes current state; it does not trust
the earlier preview.

Click **Present κ to socket**. The independent checks run, a payment is recorded,
and the socket signs ρ. The risk book still holds $75. Click **Reconcile signed ρ**:
$75 moves into settled U and Q becomes zero. **Run normal action** is a shortcut
through the complete lifecycle, stopping when human approval is needed.

Expand **Inspect bindings, signed κ / ρ, and event timeline** for raw objects,
clickable events, action export and complete tape verification. An event shows
historical evidence; it cannot undo an effect. The risk lane always shows the
current authoritative book. The editable next draft is separate from the reviewed
and signed action.

## 1:00 — Change an action and watch the gate

Create a fresh payment experiment, resolve and request authority. Select
**Increase amount after κ**, then present it. The socket refuses the $95 attempt
with `EFFECT_MISMATCH`; $75 remains reserved. Select **Normal path** and retry the
original κ, then reconcile. **Attempt replay** is refused with `REPLAY`.

The same selector offers beneficiary substitution, wrong audience, expiry, a stale
resource, pack supersession, equivalent aliases and an alias rebound. Equivalent
names for the same canonical beneficiary succeed; an alias pointing elsewhere
cannot inherit authority. A malformed attempt is not authorization to release
someone else's reservation. **Close unused authority + release** atomically
closes an unused nonce and accepts signed non-use evidence.

## 2:00 — Concurrency and uncertainty

**Launch 100 concurrent requests** opens an isolated shared-session race. Ten
requests receive real capabilities; ninety fail the authoritative collar. The
separate unsafe model admits all 100 against ten units by checking a stale value
before updating it. It has no access to a signer or protected execution path.
This experiment works in every domain with that domain's budget units.

In a fresh experiment, request κ, select **Lose execution receipt**, and execute.
The socket knows the outcome, but the institution has no accepted evidence. Q
stays held. **Recover signed ρ** reconciles the durable socket journal. Expiry
never creates free capacity by itself.

## 3:00 — Human review and referrals

**Human review experiment** prepares a new exact snapshot requiring approval.
Approve or reject the action shown in the risk lane. After approval, increasing
the amount still fails exact-effect binding. **Institution controls** lets the
operator change limits, approval thresholds, pack versions and resource state in
this selected book. Publishing a pack makes old unredeemed κ stale.

Switch to **Patient referrals** and run the normal action. Review patient P-104,
the six-record sealed manifest, North Clinic, referral purpose and consent v7.
Approve, select **Revoke consent**, and present κ: `STALE_STATE`, no disclosure.
In another fresh experiment, approve and release normally. Once the synthetic
recipient inbox receives the packet, the records/bytes count as disclosure.
Later consent revocation blocks future releases; it cannot refund past disclosure.

## 4:00 — Trading and contained scripting

In **Trading OMS / EMS**, run a normal action: the order is accepted and working,
with no fills yet. The $82,540 bound stays reserved. **Simulate 400-share fill**
settles $16,508 and leaves $66,032 reserved. **Authorize cancel remainder** issues
a separate cancellation κ; confirmed cancellation evidence releases the remainder.
Inspect its capability below the order. The actor has no direct route to the
local EMS. The backend suite also races cancellation against fills.

In **Agent runtime**, run the normal action. The fixed audit script actually
executes in Bubblewrap. Its scratch output succeeds; attempts to mutate the
institution marker, read the risk book, write outside the workspace and reach the
host network service are blocked. Results name the enforcing OS mechanism.

The coverage lab separately demonstrates an unsigned socket refusal, a narrowed
child grant, an attempted delegation expansion, and an intentionally unprotected
synthetic sink that accepts a bypass. An ordinary actor credential can exercise
the fixed `/api/coverage/unprotected-sink` counterexample without κ; it writes to a
separate dummy database. This does not change the protected risk book.

## Longer runtime recovery experiments

- **Pause before dispatch:** an intent and nonce are committed but the job has not
  launched. **Dispatch / recover job** rechecks live authority before claiming the
  launch. If the pack, grant, resource or expiry changed, launch fails closed.
  **Close unused authority + release** proves the intent was never claimed under
  the same lock used by the launcher, then releases capacity with signed evidence.
- **Interrupt after launch claim:** the outcome is ambiguous. Recovery never
  automatically launches a second job. The slot stays held; lack of evidence is
  not non-use proof. Use a fresh experiment to continue the tour, retaining this
  unresolved book as evidence.
- **Interrupt before exit receipt:** the trusted supervisor has a durable signed
  exit journal. Recovery produces/retrieves the receipt and releases the slot
  exactly once. The cumulative job-start count remains consumed.

## Reading the result

| Code or state | Meaning |
| --- | --- |
| POLICY_ALLOW / preview | Decision recorded; no executable authority in a preview |
| HUMAN_APPROVAL | Exact snapshot awaits review; no capacity allocated yet |
| SESSION_LIMIT | Current U + Q + new bound exceeds at least one named limit |
| EFFECT_MISMATCH | Invocation differs from the signed resolved effect |
| STALE_STATE | Committed resource, consent, route, grant or other state changed |
| WRONG_AUDIENCE | This socket is not the capability's recipient |
| EXPIRED | The redemption/launch validity interval ended |
| PACK_NOT_LIVE | The issuing pack was superseded |
| REPLAY | Nonce consumed or closed |
| uncertain | Required outcome evidence is missing; allocation remains held |
| partial | Some allocation remains outstanding; inspect the domain outcome for working/fill status |
| OUTCOME_UNCERTAIN | A claimed job has no accepted exit evidence; automatic relaunch is prohibited |
| DELEGATION_EXPANSION | Requested child scope exceeds the parent |

Complete tape export includes public keys and the retained head. Replaying it
verifies every decision, signature and admission/settlement allocation. Retain the
head independently to detect later truncation; no external anchor is supplied.

## Incident / Swarm Lab

See [SWARM_DEMO_GUIDE.md](SWARM_DEMO_GUIDE.md) for the fifth workspace, source context,
1,200/5,000-actor simulations, coverage comparisons and real contained-worker proof.
