from .crypto import digest
from .protocol import verify_artifact as verify
from .models import require
from .risk_book import decode
from .accounting import dimensions, vector, settlement as calculate_settlement


class Reconciler:
    def __init__(self, book, public_key, clock, registry=None):
        self.book, self.public_key, self.clock, self.registry = book, public_key, clock, registry

    def accept(self, receipt):
        if self.registry is not None:
            observer = self.registry.get(receipt.get('aud'))
            require(observer is not None, 'RECEIPT_AUDIENCE', 'No accepted observer is registered for this audience.')
            verify(receipt, observer['public'], observer['kid'], 'saac-receipt')
        else:
            verify(receipt, self.public_key, "socket-1", "saac-receipt")
        with self.book.transaction() as db:
            row = db.execute("SELECT * FROM reservations WHERE id=?", (receipt["reservation_id"],)).fetchone()
            require(row is not None, "RECEIPT_BINDING", "Receipt must reference a known reservation.")
            kappa = decode(row["kappa"])
            require(receipt["snapshot_hash"] == kappa["snapshot_hash"] and receipt["kappa_id"] == kappa.get("kappa_id", kappa["nonce"])
                    and receipt["nonce"] == kappa["nonce"] and receipt["aud"] == kappa["aud"],
                    "RECEIPT_BINDING", "Receipt does not bind this capability and snapshot.")
            nonce = db.execute("SELECT * FROM nonces WHERE nonce=?", (row["nonce"],)).fetchone()
            require(nonce["status"] != "unused" and nonce["execution_id"] == receipt["execution_id"], "RECEIPT_BINDING", "Receipt must bind durable socket redemption/non-use evidence.")
            recorded = db.execute('SELECT data FROM receipts WHERE id=? AND reservation_id=?', (receipt['receipt_id'], row['id'])).fetchone()
            require(recorded is not None and decode(recorded[0]) == receipt, 'RECEIPT_EVIDENCE', 'This local profile accepts only signed evidence retained in its socket journal.')
            rh = digest(receipt)
            rev = receipt["revision"]
            require(type(rev) is int and rev > 0, "RECEIPT_REVISION", "Receipt revisions are positive integers.")
            if rev <= row["revision"]:
                require(rev < row["revision"] or row["receipt_hash"] == rh, "RECEIPT_CONFLICT", "Equal revisions must carry identical signed evidence.")
                self.book.event(db, row["run_id"], "RECEIPT_DUPLICATE", {"receipt_id": receipt["receipt_id"], "revision": rev}, self.clock())
                return {"applied": False, "risk": self.book.risk(db)}
            settlement = receipt["settlement"]
            allocation = self.book.allocation(db, row)
            expected_totals = calculate_settlement({k: v['bound'] for k, v in allocation.items()}, receipt['result']['consumed'], receipt['result']['released'])
            require(settlement == expected_totals, 'RECEIPT_CONSERVATION', 'Result and settlement must conserve every named budget.')
            for key, old in allocation.items():
                require(settlement['consumed'][key] >= old['consumed'] and settlement['released'][key] >= old['released'],
                        'RECEIPT_REGRESSION', 'Cumulative evidence cannot undo usage or proven release.')
            expected = settlement['reservation_status']
            require(row['status'] not in ('settled', 'released'), 'RECEIPT_TERMINAL', 'A terminal reservation cannot reopen.')
            before = self.book.risk(db)
            primary = sorted(allocation)[0]
            u, released = settlement['consumed'][primary], settlement['released'][primary]
            db.execute("UPDATE reservations SET consumed=?,released=?,status=?,revision=?,receipt_hash=? WHERE id=?", (u, released, expected, rev, rh, row['id']))
            self.book.settle_dimensions(db, row, settlement)
            after = self.book.risk(db)
            require(all(v["available"] >= 0 for v in dimensions(after).values()), "INVARIANT", "Authoritative exposure exceeded a collar.")
            run = self.book.run(db, row["run_id"])
            run.update(status=expected, receipt=receipt, risk_after=after)
            self.book.save_run(db, run)
            self.book.event(db, row["run_id"], "RECEIPT_RECONCILED", {"receipt": receipt, "risk_before": before, "risk_after": after,
                "delta_u": after.get("used_cents", 0)-before.get("used_cents", 0), "delta_q": after.get("reserved_cents", 0)-before.get("reserved_cents", 0),
                "budget_deltas": {k: {"used": v["used"]-dimensions(before)[k]["used"], "reserved": v["reserved"]-dimensions(before)[k]["reserved"]} for k,v in dimensions(after).items()}}, self.clock())
            self.book.event(db, row["run_id"], "RESERVATION_SETTLED" if expected == "settled" else "RESERVATION_RELEASED" if expected == "released" else "RESERVATION_PARTIAL", {"id": row["id"], "risk": after}, self.clock())
            return {"applied": True, "risk": after}

    def uncertain(self, reservation_id):
        with self.book.transaction() as db:
            row = db.execute("SELECT * FROM reservations WHERE id=?", (reservation_id,)).fetchone()
            require(row is not None, "UNKNOWN_RESERVATION", "Reservation does not exist.")
            if row["status"] in ("settled", "released"): return
            db.execute("UPDATE reservations SET status='uncertain' WHERE id=?", (reservation_id,))
            run = self.book.run(db, row["run_id"])
            run.update(status="uncertain", risk_after=self.book.risk(db))
            self.book.save_run(db, run)
            self.book.event(db, row["run_id"], "RECONCILIATION_REQUIRED", {"id": reservation_id, "message": "Receipt is unavailable. Capacity remains reserved, including after expiry.", "risk": self.book.risk(db)}, self.clock())
