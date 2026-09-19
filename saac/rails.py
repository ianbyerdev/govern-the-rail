"""Typed consequential adapters. Only the protected socket calls execute()."""
from typing import Protocol
from .risk_book import uid
from .packs import delegation_check


class Rail(Protocol):
    def execute(self, db, effect: dict, execution_id: str, mode: str) -> dict: ...


class PaymentRail:
    def execute(self, db, effect, execution_id, mode):
        bound = effect["x"]["amount_cents"]
        consumed = 0 if mode == "reject" else bound // 2 if mode == "partial" else bound
        released = bound if mode == "reject" else 0
        if consumed:
            db.execute("INSERT INTO payments VALUES (?,?,?)", (execution_id, effect["r"], consumed))
        return {"status": "rejected" if mode == "reject" else "partial" if mode == "partial" else "paid",
                "consumed": consumed, "released": released, "beneficiary": effect["r"], "currency": effect["x"]["currency"]}


class ScopedDispatch:
    """Control-plane dispatch fixture: creates identity/scope, never arbitrary OS code."""
    def __init__(self, book, clock): self.book, self.clock = book, clock

    def execute(self, db, effect, execution_id, mode):
        parent = self.book.grant(db, effect["r"].removeprefix("grant:"))
        delegation_check(effect["x"], parent, self.clock())
        child = {**effect["x"], "id": uid("grant"), "parent_grant_id": parent["id"], "parent_version": parent["version"],
                 "socket_id": parent["socket_id"], "session_id": parent["session_id"], "depth": parent["depth"]+1,
                 "version": 1, "active": True, "chain": [*parent["chain"], parent["id"]]}
        self.book.save_grant(db, child)
        return {"status": "dispatched", "consumed": 0, "released": 0, "child_grant": child}
