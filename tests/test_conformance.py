from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
import pytest
from pydantic import ValidationError
from saac.models import Proposal, PolicyConfig, Scope, SAACError
from saac.crypto import digest
from saac.packs import assert_attenuation
from saac.tape import verify_tape
from saac.service import SAACService


def issue(svc, **kwargs): return svc.authority.propose(Proposal(**kwargs))

def update(svc, **kwargs):
    old = svc.view()["state"]["pack"]
    fields = {k: old[k] for k in PolicyConfig.model_fields}
    fields.update(version=old["version"]+1, **kwargs)
    return svc.publish(PolicyConfig(**fields))


def test_actor_snapshot_is_not_authoritative(svc):
    update(svc, session_limit_cents=10000)
    a = issue(svc, amount_cents=7500)
    b = issue(svc, amount_cents=7500, actor_risk_observed={"used_cents": -9999999, "reserved_cents": -99999, "limit_cents": 99999999})
    assert a["status"] == "authorized"
    assert b["decision"]["code"] == "SESSION_LIMIT"
    assert svc.view()["risk"]["reserved_cents"] == 7500


@pytest.mark.parametrize("artifact", [{}, {"verdict": "allow"}, {"typ": "saac-kappa", "sig": "fake"}])
def test_verdict_and_unsigned_are_not_authority(svc, artifact):
    assert svc.socket.execute(artifact, Proposal())["code"] == "INVALID_SIGNATURE"
    assert not svc.view()["payments"]


@pytest.mark.parametrize("operation", ["spawn", "shell", "raw_dispatch", "payments()"])
def test_unknown_effects_fail_closed(svc, operation):
    run = issue(svc, operation=operation)
    assert run["decision"]["code"] == "UNMEDIATED_OPERATION"
    assert "kappa" not in run


@pytest.mark.parametrize("changes,code", [
    ({"amount_cents": 7501}, "EFFECT_MISMATCH"),
    ({"amount_cents": 75000}, "EFFECT_MISMATCH"),
    ({"beneficiary": "community"}, "EFFECT_MISMATCH"),
    ({"currency": "EUR"}, "EFFECT_MISMATCH"),
    ({"purpose": "research"}, "EFFECT_MISMATCH"),
    ({"route": "local-wire"}, "EFFECT_MISMATCH"),
    ({"principal_id": "other"}, "IDENTITY_BINDING"),
    ({"agent_id": "other"}, "IDENTITY_BINDING"),
    ({"socket_id": "OTHER"}, "EFFECT_MISMATCH"),
])
def test_exact_effect_binding(svc, changes, code):
    run = issue(svc)
    result = svc.execute(run["id"], Proposal(**changes))
    assert result["attempt"]["code"] == code
    assert svc.view()["risk"]["reserved_cents"] == 7500
    assert not svc.view()["payments"]
    assert svc.execute(run["id"])["status"] == "settled"


def test_token_mutation_invalidates_signature(svc):
    run = issue(svc)
    for field, value in [("exp", 9999999999), ("nonce", "new"), ("pack_hash", "old"), ("aud", "OTHER")]:
        changed = {**run["kappa"], field: value}
        assert svc.socket.execute(changed, Proposal())["code"] == "INVALID_SIGNATURE"


def test_wrong_audience_and_replay(svc):
    run = issue(svc)
    assert svc.execute(run["id"], audience="OTHER")["attempt"]["code"] == "WRONG_AUDIENCE"
    svc.execute(run["id"])
    assert svc.execute(run["id"])["attempt"]["code"] == "REPLAY"
    assert len(svc.view()["payments"]) == 1


def test_expiry_does_not_release_then_signed_nonuse_does(svc):
    run = issue(svc, ttl_seconds=1)
    svc.advance(2)
    assert svc.execute(run["id"])["attempt"]["code"] == "EXPIRED"
    assert svc.view()["risk"]["reserved_cents"] == 7500
    svc.reconciler.uncertain(run["reservation_id"])
    svc.reconcile(run["id"])
    assert svc.view()["risk"]["available_cents"] == 50000
    assert svc.get_run(run["id"])["receipt"]["result"]["status"] == "non_use"


def test_aliases_same_canonical_effect(svc):
    a, b = issue(svc, beneficiary="  ACME  "), issue(svc, beneficiary="vendor-001")
    assert a["snapshot"]["effect"] == b["snapshot"]["effect"]
    assert a["kappa"]["effect_digest"] == b["kappa"]["effect_digest"]
    assert svc.execute(a["id"], Proposal(beneficiary="ＡＣＭＥ"))["status"] == "settled"


def test_alias_rebinding_fails(svc):
    run = issue(svc, beneficiary="payroll")
    svc.resource_update(alias_name="payroll", alias_target="beneficiary:community")
    assert svc.execute(run["id"])["attempt"]["code"] == "EFFECT_MISMATCH"


@pytest.mark.parametrize("mutation", ["resource", "route"])
def test_stale_state(svc, mutation):
    run = issue(svc)
    svc.resource_update(route="local-ach" if mutation == "route" else None)
    assert svc.execute(run["id"])["attempt"]["code"] == "STALE_STATE"


def test_pack_supersession_rollback_and_pending_approval(svc):
    run = issue(svc)
    pending = issue(svc, amount_cents=15000)
    update(svc, per_action_cents=19000)
    assert svc.execute(run["id"])["attempt"]["code"] == "PACK_NOT_LIVE"
    assert svc.authority.approve(pending["id"], pending["snapshot_hash"], True)["decision"]["code"] == "PACK_NOT_LIVE"
    with pytest.raises(SAACError, match="versions must increase"): svc.publish(PolicyConfig(version=1))


def test_exact_human_approval_and_capacity_recheck(svc):
    pending = issue(svc, amount_cents=15000)
    assert pending["status"] == "awaiting_approval" and "kappa" not in pending
    assert svc.view()["risk"]["reserved_cents"] == 0
    with pytest.raises(SAACError): svc.authority.approve(pending["id"], "different", True)
    approved = svc.authority.approve(pending["id"], pending["snapshot_hash"], True)
    assert approved["status"] == "authorized"
    assert svc.execute(pending["id"], Proposal(amount_cents=16000))["attempt"]["code"] == "EFFECT_MISMATCH"
    assert svc.execute(pending["id"])["status"] == "settled"
    with pytest.raises(SAACError): svc.authority.approve(pending["id"], pending["snapshot_hash"], True)


def test_approval_cannot_hold_stale_capacity(svc):
    update(svc, session_limit_cents=20000)
    pending = issue(svc, amount_cents=15000)
    issue(svc, amount_cents=7500)
    assert svc.authority.approve(pending["id"], pending["snapshot_hash"], True)["decision"]["code"] == "SESSION_LIMIT"


def test_approval_reject_resource_mutation_and_expiry(svc):
    pending = issue(svc, amount_cents=15000)
    svc.resource_update()
    assert svc.authority.approve(pending["id"], pending["snapshot_hash"], True)["decision"]["code"] == "STALE_STATE"
    pending = issue(svc, amount_cents=15000, ttl_seconds=1)
    svc.advance(2)
    assert svc.authority.approve(pending["id"], pending["snapshot_hash"], True)["decision"]["code"] == "EXPIRED"
    pending = issue(svc, amount_cents=15000)
    assert svc.authority.approve(pending["id"], pending["snapshot_hash"], False)["status"] == "denied"


def test_attenuating_modify_recomputes_bound(svc):
    update(svc, per_action_cents=5000, allow_attenuation=True)
    run = issue(svc)
    assert run["decision"]["verdict"] == "modify"
    assert run["kappa"]["reservation"]["bound"]["amount_cents"] == 5000
    assert svc.execute(run["id"], Proposal())["attempt"]["code"] == "EFFECT_MISMATCH"
    assert svc.execute(run["id"])["receipt"]["result"]["consumed"] == 5000
    before = run["snapshot"]["effect"]
    for field, value in [("amount_cents", 5001), ("route", "local-wire")]:
        after = deepcopy(before)
        after["x"][field] = value
        with pytest.raises(SAACError): assert_attenuation(before, after)


def test_degrade_rewrites_grant_and_needs_operator_restore(svc):
    run = issue(svc)
    svc.breaker(True)
    assert svc.view()["grants"][0]["operations"] == []
    assert issue(svc)["decision"]["verdict"] == "degrade"
    assert svc.execute(run["id"])["attempt"]["code"] == "BREAKER_HALT"
    svc.breaker(False)
    assert svc.execute(run["id"])["attempt"]["code"] == "STALE_STATE"
    assert issue(svc)["status"] == "authorized"


def test_signed_scoped_dispatch_and_child(svc):
    run = issue(svc, operation="dispatch", delegation=Scope(exp=svc.now()+60))
    assert run["kappa"]["reservation"]["bound"]["amount_cents"] == 0
    executed = svc.execute(run["id"])
    child = executed["receipt"]["result"]["child_grant"]
    assert child["session_id"] == "session.demo" and child["parent_grant_id"] == "G-DEMO"
    child_run = issue(svc, agent_id=child["agent_id"], grant_id=child["id"], amount_cents=5000)
    assert svc.execute(child_run["id"])["status"] == "settled"
    assert issue(svc, agent_id=child["agent_id"], grant_id=child["id"], amount_cents=5001)["status"] == "denied"
    svc.breaker(True)
    assert issue(svc, agent_id=child["agent_id"], grant_id=child["id"], amount_cents=100)["decision"]["code"] == "GRANT_STALE"


@pytest.mark.parametrize("changes,code", [
    ({"resources": ["beneficiary:unknown"]}, "DELEGATION_EXPANSION"),
    ({"currencies": ["EUR"]}, "DELEGATION_EXPANSION"),
    ({"purposes": ["anything"]}, "DELEGATION_EXPANSION"),
    ({"operations": ["shell"]}, "DELEGATION_EXPANSION"),
    ({"routes": ["unrestricted"]}, "DELEGATION_EXPANSION"),
    ({"principal_id": "other"}, "DELEGATION_PRINCIPAL"),
    ({"exp": 9999999999}, "DELEGATION_EXPIRY"),
])
def test_delegation_cannot_expand(svc, changes, code):
    scope = Scope(**({"exp": svc.now()+60} | changes))
    run = issue(svc, operation="dispatch", delegation=scope)
    assert run["decision"]["code"] == code
    assert "kappa" not in run


def test_partial_cancel_reordered_and_duplicated_receipts(svc):
    run = issue(svc)
    first = svc.socket.execute(run["kappa"], Proposal(), mode="partial")["receipt"]
    assert svc.view()["risk"]["reserved_cents"] == 7500  # actor report != evidence
    final = svc.socket.evidence(run["reservation_id"], cancel=True)
    svc.reconciler.accept(final)  # later cumulative evidence arrives first
    assert svc.view()["risk"]["used_cents"] == 3750
    assert svc.view()["risk"]["reserved_cents"] == 0
    assert not svc.reconciler.accept(first)["applied"]
    assert not svc.reconciler.accept(final)["applied"]
    assert svc.view()["risk"]["available_cents"] == 46250


def test_partial_keeps_remainder_live(svc):
    run = issue(svc)
    svc.execute(run["id"], mode="partial")
    assert svc.view()["risk"]["reserved_cents"] == 3750
    assert svc.view()["risk"]["used_cents"] == 3750
    svc.reconcile(run["id"], cancel=True)
    assert svc.view()["risk"]["reserved_cents"] == 0


def test_rail_rejection_signed_release(svc):
    run = issue(svc)
    svc.execute(run["id"], mode="reject")
    assert svc.view()["risk"]["available_cents"] == 50000
    assert not svc.view()["payments"]


def test_receipt_loss_expiry_and_restart(svc):
    run = issue(svc, ttl_seconds=1)
    svc.execute(run["id"], lose_receipt=True)
    svc.advance(2)
    assert svc.view()["risk"]["used_cents"] == 0
    assert svc.view()["risk"]["reserved_cents"] == 7500
    restarted = SAACService(svc.directory, clock=lambda: 1800000000)
    assert restarted.view()["risk"]["reserved_cents"] == 7500
    restarted.reconcile(run["id"])
    assert restarted.view()["risk"]["used_cents"] == 7500
    assert restarted.view()["risk"]["reserved_cents"] == 0


def test_forged_and_conflicting_receipts(svc):
    run = issue(svc)
    rho = svc.socket.execute(run["kappa"], Proposal())["receipt"]
    bad = deepcopy(rho)
    bad["settlement"]["released"]["amount_cents"] = 7500
    with pytest.raises(SAACError): svc.reconciler.accept(bad)
    # Even a trusted signer cannot accidentally violate conservation.
    with pytest.raises(SAACError): svc.reconciler.accept(svc.socket_signer.sign({k:v for k,v in bad.items() if k != "sig"}))
    svc.reconciler.accept(rho)
    conflict = svc.socket_signer.sign({**{k:v for k,v in rho.items() if k != "sig"}, "ts": rho["ts"]+1})
    with pytest.raises(SAACError): svc.reconciler.accept(conflict)


def test_signing_failure_rolls_back_reservation(svc, monkeypatch):
    def fail(*args): raise RuntimeError("signer offline")
    monkeypatch.setattr(svc.issuer, "sign", fail)
    with pytest.raises(RuntimeError): issue(svc)
    assert svc.view()["risk"]["reserved_cents"] == 0
    assert svc.view()["reservations"] == []


def test_execution_exception_rolls_back_then_conservative_recovery(svc, monkeypatch):
    run = issue(svc)
    rail = svc.socket.rails["payment"]
    original = rail.execute
    def fail(db, e, eid, mode):
        original(db, e, eid, mode)
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(rail, "execute", fail)
    with pytest.raises(RuntimeError): svc.execute(run["id"])
    assert not svc.view()["payments"]
    assert svc.view()["risk"]["reserved_cents"] == 7500
    svc.reconcile(run["id"])
    assert svc.view()["risk"]["available_cents"] == 50000


def test_100_simultaneous_requests_and_mixed_receipt_delivery(svc):
    update(svc, session_limit_cents=1000, per_action_cents=100, approval_above_cents=100)
    gate = Barrier(100)
    def work(_):
        gate.wait(timeout=20)
        return issue(svc, amount_cents=100)
    with ThreadPoolExecutor(max_workers=100) as pool: runs = list(pool.map(work, range(100)))
    winners = [r for r in runs if "kappa" in r]
    assert len(winners) == 10
    assert svc.view()["risk"]["reserved_cents"] == 1000
    receipts = []
    for i,r in enumerate(winners):
        rho = svc.socket.execute(r["kappa"], Proposal(amount_cents=100), mode="partial" if i%2 else "full")["receipt"]
        receipts.append(rho)
        svc.reconciler.uncertain(r["reservation_id"])
        assert svc.view()["risk"]["used_cents"]+svc.view()["risk"]["reserved_cents"] <= 1000
    for rho in reversed(receipts[1:]):
        svc.reconciler.accept(rho)
        svc.reconciler.accept(rho)
    assert svc.view()["risk"]["available_cents"] == 0
    for r in winners: svc.reconcile(r["id"], cancel=True)
    assert svc.view()["risk"]["used_cents"] == 750
    assert svc.view()["risk"]["reserved_cents"] == 0
    with svc.book.transaction() as db: events = svc.book.events(db)
    report = verify_tape(events, svc.view()["keys"])
    assert report["decisions_replayed"] == 100
    assert report["capabilities_verified"] == 10
    assert report["used_cents"] == 750 and not report["unresolved"]


def test_concurrent_nonce_redemption(svc):
    run = issue(svc)
    gate = Barrier(20)
    def work(_):
        gate.wait(timeout=10)
        return svc.socket.execute(run["kappa"], Proposal())
    with ThreadPoolExecutor(max_workers=20) as pool: results = list(pool.map(work, range(20)))
    assert sum(r["accepted"] for r in results) == 1
    assert sum(r["code"] == "REPLAY" for r in results) == 19
    assert len(svc.view()["payments"]) == 1
    svc.reconcile(run["id"])


def test_tape_tamper_and_retained_checkpoint(svc):
    svc.execute(issue(svc)["id"])
    with svc.book.transaction() as db: events = svc.book.events(db)
    keys = svc.view()["keys"]
    report = verify_tape(events, keys)
    assert report["valid"]
    with pytest.raises(SAACError): verify_tape(events[:-1], keys, report["head"])
    altered = deepcopy(events)
    altered[1]["data"]["amount_cents"] = 1
    with pytest.raises(SAACError): verify_tape(altered, keys)


@pytest.mark.parametrize("amount", [1.1, "100", True, -1, 0])
def test_noncanonical_amounts_fail(amount):
    with pytest.raises(ValidationError): Proposal(amount_cents=amount)


def test_limit_cannot_drop_below_exposure(svc):
    issue(svc)
    with pytest.raises(SAACError): update(svc, session_limit_cents=100)


def test_socket_checks_expiry_after_waiting_for_transaction(tmp_path, monkeypatch):
    from threading import Event
    import saac.protected_socket as socket_module
    clock = [1800000000]
    svc = SAACService(tmp_path, clock=lambda: clock[0])
    run = issue(svc, ttl_seconds=1)
    signature_done = Event()
    original_verify = socket_module.verify
    def verified(*args):
        original_verify(*args)
        signature_done.set()
    monkeypatch.setattr(socket_module, "verify", verified)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with svc.book.transaction():
            future = pool.submit(svc.socket.execute, run["kappa"], Proposal())
            assert signature_done.wait(timeout=5)
            clock[0] += 2
        result = future.result(timeout=5)
    assert result["code"] == "EXPIRED"
    assert not svc.view()["payments"]
    assert svc.view()["risk"]["reserved_cents"] == 7500


def test_descendant_amount_expansion_denied(svc):
    parent = svc.execute(issue(svc, operation="dispatch", delegation=Scope(exp=svc.now()+60, operations=["payment", "dispatch"]))["id"])["receipt"]["result"]["child_grant"]
    run = issue(svc, operation="dispatch", agent_id=parent["agent_id"], grant_id=parent["id"], delegation=Scope(agent_id="grandchild", exp=svc.now()+30, max_amount_cents=5001))
    assert run["decision"]["code"] == "DELEGATION_EXPANSION"
