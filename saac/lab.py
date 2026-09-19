"""Operator-controlled experiments. Each uses an isolated real SAAC book."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from .service import SAACService
from .models import Proposal, PolicyConfig, Scope, require
from .risk_book import uid
from .tape import verify_tape

SCENARIOS = {
    "normal": ("Normal execution", "A proposal clears policy, reserves $75, then executes and settles through signed evidence."),
    "modify-up": ("Modify-up attack", "κ binds $75. Presenting $95 fails exact-effect verification; the original $75 remains reserved."),
    "beneficiary": ("Beneficiary substitution", "Authority for Acme cannot pay Community Lab, even though both beneficiaries are generally allowed."),
    "replay": ("Replay", "The first redemption pays once. The second presentation of the same nonce is refused."),
    "audience": ("Wrong audience", "A valid signature does not make κ portable to another socket."),
    "expired": ("Expired capability", "Time stops new redemption. It does not establish non-use or release capacity."),
    "aliases": ("Equivalent aliases", "acme and vendor-001 resolve to one canonical beneficiary. The same exact effect can execute through either spelling."),
    "alias-rebound": ("Alias rebound", "The institution rebinds payroll after issuance. Re-resolution finds a different beneficiary and refuses the old κ."),
    "stale": ("Stale resource", "The beneficiary version changes after issuance. A fresh snapshot and mediation are required."),
    "pack": ("Pack supersession", "A newly published pack makes the previous hash non-live. Its signed capabilities fail closed."),
    "delegation": ("Narrow delegation", "A mediated scoped dispatch creates a $50 child grant in the parent's session. The child then pays $40."),
    "expansion": ("Delegation expansion", "The child asks for a beneficiary outside the parent grant. No capability is issued."),
    "receipt-loss": ("Lost receipt", "The socket has paid $75, but the risk book has not received evidence. $75 stays reserved until reconciliation."),
    "partial": ("Partial execution", "A $75 payment executes $37.50. The still-live $37.50 remains reserved until the socket proves cancellation."),
    "human": ("Exact human approval", "Review the resolved $150 snapshot. Approval can issue authority only for that effect and live state."),
    "attenuate": ("Safe modify", "The pack reduces a $75 proposal to $50 and reserves that new bound. Authority cannot grow through modify."),
    "race": ("100 concurrent requests", "Ten dollars of capacity, 100 simultaneous one-dollar proposals. SAAC can issue exactly ten capabilities."),
}


def config(svc, **changes):
    pack = svc.view()["state"]["pack"]
    values = {k:pack[k] for k in PolicyConfig.model_fields}
    values.update(version=pack["version"]+1, **changes)
    return PolicyConfig(**values)


def race(svc):
    svc.publish(config(svc, session_limit_cents=1000, per_action_cents=100, approval_above_cents=100))
    start = Barrier(100)
    def request(i):
        start.wait(timeout=30)
        run = svc.authority.propose(Proposal(amount_cents=100))
        return {"index": i, "accepted": "kappa" in run, "proposal_id": run["id"], "code": run["decision"]["code"]}
    with ThreadPoolExecutor(max_workers=100) as pool: results = list(pool.map(request, range(100)))
    # Deliberately unsafe comparison: isolated memory, no signature or socket access.
    observed = Barrier(100)
    lock = Lock()
    unsafe = {"used": 0}
    def naive(_):
        passes = unsafe["used"]+100 <= 1000
        observed.wait(timeout=30)  # all 100 inspect the same stale value
        if passes:
            with lock: unsafe["used"] += 100
        return passes
    with ThreadPoolExecutor(max_workers=100) as pool: naive_results = list(pool.map(naive, range(100)))
    return {"requests": results, "accepted": sum(r["accepted"] for r in results), "denied": sum(not r["accepted"] for r in results),
            "limit_cents": 1000, "reserved_cents": svc.view()["risk"]["reserved_cents"],
            "unsafe": {"accepted": sum(naive_results), "exposure_cents": unsafe["used"], "oversubscribed_cents": unsafe["used"]-1000,
                       "label": "INTENTIONALLY UNSAFE • isolated in-memory check-then-act"}}


def run_scenario(directory, scenario):
    require(scenario in SCENARIOS, "UNKNOWN_SCENARIO", "Unknown lab experiment.")
    book_id = uid("lab")
    svc = SAACService(directory / book_id)
    proposal = Proposal()
    extra = {}
    if scenario == "race":
        extra["race"] = race(svc)
        run = svc.get_run(next(r["proposal_id"] for r in extra["race"]["requests"] if r["accepted"]))
    else:
        if scenario == "human": proposal = Proposal(amount_cents=15000)
        if scenario in ("delegation", "expansion"):
            scope = Scope(exp=svc.now()+300, resources=["beneficiary:unknown"] if scenario == "expansion" else ["beneficiary:acme"])
            proposal = Proposal(operation="dispatch", delegation=scope)
        if scenario == "alias-rebound": proposal = Proposal(beneficiary="payroll")
        if scenario == "attenuate": svc.publish(config(svc, per_action_cents=5000, allow_attenuation=True))
        run = svc.authority.propose(proposal)
        rid = run["id"]
        if scenario == "modify-up": run = svc.execute(rid, Proposal(amount_cents=9500))
        elif scenario == "beneficiary": run = svc.execute(rid, Proposal(beneficiary="community"))
        elif scenario == "audience": run = svc.execute(rid, audience="PAYMENTS-OTHER")
        elif scenario == "expired":
            svc.advance(121)
            run = svc.execute(rid)
            svc.reconciler.uncertain(run["reservation_id"])
        elif scenario == "aliases": run = svc.execute(rid, Proposal(beneficiary="vendor-001"))
        elif scenario == "alias-rebound":
            svc.resource_update(alias_name="payroll", alias_target="beneficiary:community")
            run = svc.execute(rid)
        elif scenario == "stale":
            svc.resource_update()
            run = svc.execute(rid)
        elif scenario == "pack":
            svc.publish(config(svc, per_action_cents=19000))
            run = svc.execute(rid)
        elif scenario == "receipt-loss": run = svc.execute(rid, lose_receipt=True)
        elif scenario == "partial": run = svc.execute(rid, mode="partial")
        elif scenario == "delegation":
            parent_run = svc.execute(rid)
            child = parent_run["receipt"]["result"]["child_grant"]
            extra["dispatch_run"] = parent_run
            child_run = svc.authority.propose(Proposal(agent_id=child["agent_id"], grant_id=child["id"], amount_cents=4000))
            run = svc.execute(child_run["id"])
        elif scenario not in ("human", "expansion"):
            run = svc.execute(rid)
            if scenario == "replay": run = svc.execute(rid)
        run = svc.get_run(run["id"])
    with svc.book.transaction() as db: events = svc.book.events(db)
    return {"book_id": book_id, "scenario": scenario, "title": SCENARIOS[scenario][0], "explanation": SCENARIOS[scenario][1],
            "run": run, "view": svc.view(), "verification": verify_tape(events, svc.view()["keys"]), **extra}
