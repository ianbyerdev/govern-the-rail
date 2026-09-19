from .crypto import digest
from .protocol import verify_artifact as verify
from .models import Proposal, SAACError, require
from .resolver import resolve
from .authority import live_grant
from .risk_book import uid, encode, decode
from .receipts import make_receipt
from .rails import PaymentRail, ScopedDispatch
from .domain_models import parse_proposal
from .accounting import vector
from .domain_rails import OrderRail, CancelOrderRail, ReferralRail, JobRail, JobDelegationRail


class ProtectedSocket:
    def __init__(self, book, public_key, receipt_signer, clock, audience="PAYMENTS-1"):
        self.book, self.public_key, self.signer, self.clock, self.audience = book, public_key, receipt_signer, clock, audience
        self.rails = {"payment": PaymentRail(), "dispatch": ScopedDispatch(book, clock)}
        self.rails.update({'order.submit': OrderRail(), 'order.cancel': CancelOrderRail(), 'referral.release': ReferralRail(book),
                           'job.run': JobRail(), 'job.delegate': JobDelegationRail(book, clock)})

        from .swarm.rails import SwarmRail
        from .swarm.models import OPERATIONS
        self.rails.update({op: SwarmRail(book, clock) for op in OPERATIONS})

    def execute(self, kappa: dict, proposal: Proposal, mode="full"):
        proposal = parse_proposal(proposal)
        require(mode in ("full", "partial", "reject"), "MODE", "Unknown simulated rail result.")
        now, run_id, signature_valid = self.clock(), None, False
        try:
            # Verify independently, using only the risk book's PUBLIC key.
            verify(kappa, self.public_key, "riskbook-1", "saac-kappa")
            signature_valid = True
            with self.book.transaction() as db:
                now = self.clock()  # Check expiry AFTER acquiring the redemption lock.
                row = db.execute("SELECT * FROM reservations WHERE id=?", (kappa["reservation"]["id"],)).fetchone()
                require(row is not None and decode(row["kappa"]) == kappa, "NO_RESERVATION", "The signed authority must match a committed reservation.")
                run_id = row["run_id"]
                require(kappa["aud"] == self.audience, "WRONG_AUDIENCE", "This capability is addressed to a different protected socket.")
                require(kappa["iat"] <= now < kappa["exp"], "EXPIRED", "This capability is outside its validity interval.")
                require(kappa["principal_id"] == proposal.principal_id and kappa["agent_id"] == proposal.agent_id and kappa["grant_id"] == proposal.grant_id,
                        "IDENTITY_BINDING", "The submitted identity and grant must match signed authority.")
                nonce = db.execute("SELECT status FROM nonces WHERE nonce=?", (kappa["nonce"],)).fetchone()
                require(nonce is not None and nonce[0] == "unused", "REPLAY", "This single-use capability has already been redeemed or closed.")
                require(row["status"] in ("reserved", "uncertain") and row["consumed"] == row["released"] == 0,
                        "RESERVATION_INACTIVE", "The capability requires a live, unspent reservation.")
                allocation = self.book.allocation(db, row)
                require(set(allocation) == set(kappa['reservation']['bound']) and
                        all(v['consumed'] == v['released'] == 0 and v['bound'] == kappa['reservation']['bound'][key]
                            for key, v in allocation.items()),
                        "RESERVATION_INACTIVE", "The live allocation must match the signed bound in every dimension.")
                state = self.book.state(db)
                verify(state["pack"], self.public_key, "riskbook-1", "saac-pack")
                require(digest(state["pack"]) == state["pack_hash"] == kappa["pack_hash"], "PACK_NOT_LIVE", "The issuing pack is no longer live; request new mediation.")
                require(not state["breaker"], "BREAKER_HALT", "The institutional breaker refuses execution.")
                grant = live_grant(self.book, db, proposal.grant_id, now)
                current = resolve(proposal, state, grant, db).model_dump()
                require(current["c"] == kappa["effect"]["c"], "STALE_STATE", "The resource, route, reference or grant version changed after mediation.")
                require(digest(current) == kappa["effect_digest"] and current == kappa["effect"], "EFFECT_MISMATCH", "This is not the exact resolved effect authorized by κ.")
                run = self.book.run(db, run_id)
                require(digest(run["snapshot"]) == kappa["snapshot_hash"], "SNAPSHOT_BINDING", "The committed snapshot no longer matches κ.")
                self.book.event(db, run_id, "CAPABILITY_VERIFIED", {"signature_valid": True, "checks": ["signature", "reservation", "audience", "identity", "expiry", "nonce", "pack_liveness", "live_resource", "exact_effect", "snapshot"], "effect_digest": digest(current)}, now)
                execution_id = uid("execution")
                db.execute("UPDATE nonces SET status='consumed',execution_id=? WHERE nonce=?", (execution_id, kappa["nonce"]))
                db.execute("UPDATE reservations SET status='executing' WHERE id=?", (row["id"],))
                self.book.event(db, run_id, "EXECUTION_STARTED", {"execution_id": execution_id, "effect": current}, now)
                result = self.rails[current["o"]].execute(db, current, execution_id, mode)
                if current['o'] == 'agent.stop':
                    self._child_receipt(db, result['child'], now)
                if current['o'] == 'order.cancel':
                    self._order_receipt(db, result['order'], now)
                execution = {"id": execution_id, "revision": 1, "result": result}
                self.book.event(db, run_id, 'DISPATCH_INTENT_COMMITTED' if current['o'] == 'job.run' else 'EXECUTION_COMPLETED', execution, now)
                receipt = self._persist(db, row, execution, now)
                run.update(status="executed", socket_decision={"code": "EXECUTED", "audience": self.audience, "signature_valid": True, "message": "The socket verified κ and executed its exact effect."}, execution=execution)
                self.book.save_run(db, run)
            return {"accepted": True, "code": "EXECUTED", "receipt": receipt, "execution": execution}
        except SAACError as error:
            with self.book.transaction() as db:
                if run_id:
                    run = self.book.run(db, run_id)
                    run["socket_decision"] = {"code": error.code, "message": error.message, "audience": self.audience, "accepted": False, "signature_valid": signature_valid}
                    self.book.save_run(db, run)
                self.book.event(db, run_id, "SOCKET_DENIED", {"code": error.code, "message": error.message, "signature_valid": signature_valid}, now)
            return {"accepted": False, "code": error.code, "message": error.message, "signature_valid": signature_valid}

    def _persist(self, db, row, execution, now):
        kappa = decode(row["kappa"])
        receipt = make_receipt(self.signer, kappa, execution, now)
        db.execute("INSERT OR REPLACE INTO executions VALUES (?,?,?)", (execution["id"], row["id"], encode(execution)))
        db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (receipt["receipt_id"], row["id"], receipt["revision"], encode(receipt)))
        self.book.event(db, row["run_id"], "RECEIPT_CREATED", receipt, now)
        run = self.book.run(db, row['run_id'])
        run['execution'] = execution
        self.book.save_run(db, run)
        return receipt

    def _child_receipt(self, db, child, now):
        row = db.execute('SELECT r.* FROM reservations r JOIN executions e ON e.reservation_id=r.id WHERE e.id=?', (child['execution_id'],)).fetchone()
        execution = decode(db.execute('SELECT data FROM executions WHERE id=?', (child['execution_id'],)).fetchone()[0])
        execution['revision'] += 1
        execution['result'].update(status='stopped', child=child)
        execution['result']['released']['agent_slots'] = 1
        self.book.event(db, row['run_id'], 'CHILD_EXIT_OBSERVED', child, now)
        return self._persist(db, row, execution, now)

    def _order_receipt(self, db, order, now):
        row = db.execute('SELECT r.* FROM reservations r JOIN executions e ON e.reservation_id=r.id WHERE e.id=?', (order['execution_id'],)).fetchone()
        execution = decode(db.execute('SELECT data FROM executions WHERE id=?', (order['execution_id'],)).fetchone()[0])
        bound = decode(row['kappa'])['reservation']['bound']['notional_usd_cents']
        remaining = (order['quantity']-order['filled'])*order['limit_price_cents'] if order['status'] in ('working', 'partial') else 0
        execution['revision'] += 1
        execution['result'] = {'status': order['status'], 'order': order, 'consumed': {'notional_usd_cents': order['notional']},
                               'released': {'notional_usd_cents': bound-order['notional']-remaining}}
        return self._persist(db, row, execution, now)

    def fill_order(self, order_id, quantity, price_cents):
        """Trusted local EMS progression, bounded by the already admitted order."""
        with self.book.transaction() as db:
            row = db.execute('SELECT data FROM orders WHERE id=?', (order_id,)).fetchone()
            require(row is not None, 'UNKNOWN_ORDER', 'Unknown order.')
            order = decode(row[0])
            require(order['status'] in ('working', 'partial'), 'ORDER_TERMINAL', 'The EMS cannot fill a closed order.')
            require(type(quantity) is int and 0 < quantity <= order['quantity']-order['filled'], 'FILL_QUANTITY', 'A fill cannot exceed the live remainder.')
            require(type(price_cents) is int and 0 < price_cents <= order['limit_price_cents'], 'FILL_PRICE', 'A buy fill cannot exceed its admitted limit.')
            order.update(filled=order['filled']+quantity, notional=order['notional']+quantity*price_cents, version=order['version']+1)
            order['status'] = 'filled' if order['filled'] == order['quantity'] else 'partial'
            db.execute('UPDATE orders SET data=? WHERE id=?', (encode(order), order_id))
            self.book.event(db, None, 'EMS_FILL', order, self.clock())
            return self._order_receipt(db, order, self.clock())

    def evidence(self, reservation_id, cancel=False):
        """Accepted observer: durable receipt or an atomically closed non-use proof."""
        with self.book.transaction() as db:
            row = db.execute("SELECT * FROM reservations WHERE id=?", (reservation_id,)).fetchone()
            require(row is not None, "UNKNOWN_RESERVATION", "Reservation does not exist.")
            prior = db.execute("SELECT data FROM executions WHERE reservation_id=?", (reservation_id,)).fetchone()
            if prior:
                execution = decode(prior[0])
                require(not cancel or 'amount_cents' in decode(row['kappa'])['reservation']['bound'], 'CANCEL_AUTHORITY_REQUIRED', 'A working order needs a separately authorized cancellation; dispatch and disclosure cannot be refunded.')
                remaining = row["bound"]-execution["result"]["consumed"]-execution["result"]["released"] if type(execution["result"]["consumed"]) is int else 0
                if cancel and remaining:
                    execution["revision"] += 1
                    execution["result"]["released"] += remaining
                    execution["result"]["status"] = "cancelled"
                    return self._persist(db, row, execution, self.clock())
                return decode(db.execute("SELECT data FROM receipts WHERE reservation_id=? ORDER BY revision DESC LIMIT 1", (reservation_id,)).fetchone()[0])
            nonce = db.execute("SELECT status FROM nonces WHERE nonce=?", (row["nonce"],)).fetchone()
            require(nonce is not None and nonce[0] == "unused", "OUTCOME_UNCERTAIN", "Redemption may have occurred; no accepted outcome is available.")
            # Non-use evidence is not a timer: close redemption and sign under the same lock.
            bound = decode(row['kappa'])['reservation']['bound']
            consumed, released = ({k: 0 for k in bound}, bound) if 'amount_cents' not in bound else (0, row['bound'])
            execution = {"id": uid("execution"), "revision": 1, "result": {"status": "non_use", "consumed": consumed, "released": released}}
            db.execute("UPDATE nonces SET status='closed',execution_id=? WHERE nonce=?", (execution["id"], row["nonce"]))
            return self._persist(db, row, execution, self.clock())
