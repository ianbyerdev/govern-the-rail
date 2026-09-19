from copy import deepcopy
from .crypto import digest
from .protocol import verify_artifact as verify
from .models import Proposal, SAACError, require
from .packs import evaluate, assert_attenuation
from .resolver import resolve, SCHEMA_HASH
from .risk_book import uid, encode
from .domain_models import parse_proposal
from .accounting import admission, vector
from .protocol import WIRE_VERSION


def request_intent(proposal):
    """Informational provenance and actor observations cannot multiply authority."""
    return proposal.model_dump(exclude={"harness_id", "model_id", "actor_risk_observed"})


def live_grant(book, db, gid, now):
    grant = book.grant(db, gid)
    require(grant is not None, "UNKNOWN_GRANT", "No independently issued grant exists for this identity.")
    current = grant
    seen = set()
    while current:
        require(current["id"] not in seen, "GRANT_CHAIN", "Delegation chains must be acyclic.")
        seen.add(current["id"])
        require(current["active"] and now < current["exp"], "GRANT_INACTIVE", "This grant or an ancestor is expired or revoked.")
        if not current["parent_grant_id"]: break
        parent = book.grant(db, current["parent_grant_id"])
        require(parent is not None and parent["version"] == current["parent_version"], "GRANT_STALE", "Parent authority changed; child needs independent re-mediation.")
        current = parent
    return grant


class Authority:
    def __init__(self, book, signer, clock):
        self.book, self.signer, self.clock = book, signer, clock

    def propose(self, proposal: Proposal, preview=False, idempotency_key=None, *, request_id=None):
        if isinstance(proposal, Proposal):
            proposal = Proposal.model_validate(proposal.model_dump())
        else:
            proposal = parse_proposal(proposal)
        require(request_id is None or (isinstance(request_id, str) and 1 <= len(request_id) <= 128
                and request_id.isascii() and all(c.isalnum() or c in "._:-" for c in request_id)),
                "REQUEST_ID", "request_id must be 1–128 ASCII letters, digits, dots, underscores, colons or hyphens.")
        require(request_id is None or idempotency_key is None, "REQUEST_ID", "Supply one request identifier.")
        request_id = request_id if request_id is not None else idempotency_key or uid("request")
        identity = {k: getattr(proposal, k) for k in ("principal_id", "agent_id", "grant_id")}
        key = "saac-request:" + digest({**identity, "request_id": request_id, "preview": bool(preview)})
        with self.book.transaction() as db:
            # Lookup, CHECK and RESERVE share the same durable serialization point.
            old = db.execute('SELECT * FROM request_keys WHERE id=?', (key,)).fetchone()
            if old is None and idempotency_key:
                # Campaign request identities remain stable across retries.
                old = db.execute('SELECT * FROM request_keys WHERE id=?', (idempotency_key,)).fetchone()
            if old:
                previous = self.book.run(db, old['run_id'])
                self._check_retry(db, proposal, previous, identity, preview)
                self.book.event(db, previous['id'], 'AUTHORIZATION_REPLAYED',
                                {'request_id': request_id, 'reservation_id': previous.get('reservation_id'),
                                 'kappa_id': previous.get('kappa', {}).get('kappa_id'),
                                 'new_allocation': False}, self.clock())
                return previous
            now = self.clock()
            run = {"id": uid("proposal"), "request_id": request_id, "preview": bool(preview),
                   "proposal": proposal.model_dump(), "status": "proposed", "created_at": now}
            self.book.event(db, run["id"], "PROPOSAL_CREATED", run["proposal"], now)
            try:
                state = self.book.state(db)
                grant = live_grant(self.book, db, proposal.grant_id, now)
                require(proposal.agent_id == grant["agent_id"], "AGENT_BINDING", "This agent does not own the grant.")
                effect = resolve(proposal, state, grant, db).model_dump()
                # Keep the requested canonical effect even if policy later attenuates it.
                run["resolved_request"] = deepcopy(effect)
                seq = self.book.event(db, run["id"], "EFFECT_RESOLVED", effect, now)
                snapshot = {"saac_version": WIRE_VERSION, "snapshot_id": uid("snapshot"), "seq": seq,
                    "ts": now, "exp": min(now+proposal.ttl_seconds, now+state["pack"]["max_ttl_seconds"], grant["exp"]),
                    "nonce": uid("nonce"), "agent_id": proposal.agent_id, "harness_id": proposal.harness_id,
                    "model_id": proposal.model_id, "principal_id": proposal.principal_id, "grant_id": grant["id"],
                    "parent_grant_id": grant["parent_grant_id"], "delegation_chain": grant["chain"],
                    "socket_id": proposal.socket_id, "tool": {"name": proposal.operation, "schema_hash": effect['c']['schema_hash']},
                    "declared_effect_class": proposal.operation, "effect_class": effect["o"], "args": effect["x"],
                    "destination": effect["x"].get("route", effect["r"]), "resource": {"id": effect["r"], "state": effect["c"]},
                    "pack_id": state["pack"]["pack_id"], "pack_hash": state["pack_hash"],
                    "session_risk_observed": proposal.actor_risk_observed, "breaker": state["breaker"],
                    "provenance": ["untrusted:actor-proposal"], "host_commitment": "complete_for_effect", "effect": effect}
                run["snapshot"] = snapshot
                self._issue(db, run, approved=False, commit=not preview)
            except SAACError as e:
                run.update(status="denied", decision={"verdict": "deny", "code": e.code, "message": e.message})
                self.book.event(db, run["id"], "AUTHORITY_DENIED", run["decision"], now)
            self.book.save_run(db, run)
            db.execute('INSERT INTO request_keys VALUES (?,?,?)', (key, run['id'], digest(proposal.model_dump())))
        return run

    def _check_retry(self, db, proposal, previous, identity, preview):
        require(all(previous['proposal'][k] == v for k, v in identity.items())
                and previous.get('preview', previous['status'] == 'preview') == bool(preview),
                'IDEMPOTENCY_CONFLICT', 'A request identity belongs to one authenticated context and request mode.')
        original = parse_proposal(previous['proposal'])
        if request_intent(proposal) == request_intent(original):
            # Return the original artifact even after expiry, revocation or execution.
            # It is never renewed here; the rail still checks live authority and nonce.
            return
        original_effect = previous.get('resolved_request', previous.get('snapshot', {}).get('effect'))
        same = False
        if original_effect is not None and proposal.ttl_seconds == original.ttl_seconds:
            try:
                state = self.book.state(db)
                grant = self.book.grant(db, proposal.grant_id)
                same = grant is not None and resolve(proposal, state, grant, db).model_dump() == original_effect
            except SAACError:
                pass
        require(same, 'IDEMPOTENCY_CONFLICT', 'A request identity cannot authorize a changed canonical effect; use a new request_id for new intent.')

    def _issue(self, db, run, approved, commit=True):
        now, snapshot = self.clock(), run["snapshot"]
        state = self.book.state(db)
        verify(state["pack"], self.signer.public, self.signer.kid, "saac-pack")
        require(digest(state["pack"]) == state["pack_hash"], "PACK_INTEGRITY", "Pack content does not match its live hash.")
        grant = live_grant(self.book, db, snapshot["grant_id"], now)
        require(now < snapshot["exp"], "EXPIRED", "This reviewed snapshot has expired.")
        require(snapshot["pack_hash"] == state["pack_hash"], "PACK_NOT_LIVE", "The snapshot's pack has been superseded.")
        proposal = parse_proposal(run.get("execution_proposal", run["proposal"]))
        current = resolve(proposal, state, grant, db).model_dump()
        require(current == snapshot["effect"], "STALE_STATE", "Policy-relevant state changed; resolve and review a new snapshot.")
        risk = self.book.risk(db)
        inputs = {"effect": current, "pack": state["pack"], "grant": grant, "risk": risk, "now": now, "approved": approved, "breaker": state["breaker"]}
        decision = evaluate(**inputs)
        self.book.event(db, run["id"], "POLICY_EVALUATED", {"inputs": inputs, "decision": decision}, now)
        run["decision"], run["risk_before"] = decision, risk
        if decision["verdict"] in ("deny", "degrade"):
            run["status"] = "denied"
            return
        if current != decision["effect"]:
            assert_attenuation(current, decision["effect"])
            snapshot["effect"] = decision["effect"]
            snapshot["args"] = decision["effect"]["x"]
            proposal = proposal.model_copy(update={"amount_cents": decision["bound"]})
            run["execution_proposal"] = proposal.model_dump()
        run["snapshot_hash"] = digest(snapshot)
        if not commit:
            run['status'] = 'preview'
            self.book.event(db, run['id'], 'VERDICT_PREVIEW', {'message': 'This preview is not authority. Issuance must re-evaluate current state.'}, now)
            return
        if decision["verdict"] == "escalate":
            run["status"] = "awaiting_approval"
            self.book.event(db, run["id"], "APPROVAL_REQUIRED", {"snapshot_hash": run["snapshot_hash"], "snapshot": snapshot}, now)
            return
        # Serialization is held from state re-read through durable reservation + κ.
        bound, rid = decision["bound"], uid("reservation")
        admission(risk, bound)
        seq = self.book.event(db, run["id"], "RISK_RESERVED", {"id": rid, "bound": bound, "risk_before": risk}, now)
        kappa = self.signer.sign({"typ": "saac-kappa", "kappa_id": uid("capability"), "aud": snapshot["socket_id"], "iat": now, "exp": snapshot["exp"],
            "nonce": snapshot["nonce"], "grant_id": snapshot["grant_id"], "principal_id": snapshot["principal_id"],
            "agent_id": snapshot["agent_id"], "pack_hash": snapshot["pack_hash"], "effect_digest": digest(snapshot["effect"]),
            "effect": snapshot["effect"], "snapshot_hash": run["snapshot_hash"], "snapshot_id": snapshot["snapshot_id"],
            "delegation_chain": snapshot["delegation_chain"], "reservation": {"id": rid, "book_seq": seq, "bound": vector(bound)}})
        primary = vector(bound)[sorted(vector(bound))[0]]
        db.execute("INSERT INTO reservations(id,run_id,nonce,bound,status,kappa) VALUES (?,?,?,?,?,?)", (rid, run["id"], snapshot["nonce"], primary, "reserved", encode(kappa)))
        self.book.reserve_dimensions(db, rid, bound)
        db.execute("INSERT INTO nonces VALUES (?, 'unused', NULL)", (snapshot["nonce"],))
        run.update(status="authorized", kappa=kappa, reservation_id=rid, risk_after=self.book.risk(db))
        self.book.event(db, run["id"], "CAPABILITY_ISSUED", kappa, now)

    def approve(self, run_id, snapshot_hash, approve):
        with self.book.transaction() as db:
            run = self.book.run(db, run_id)
            require(run is not None and run["status"] == "awaiting_approval", "APPROVAL_STATE", "Only a pending exact snapshot can be reviewed once.")
            require(snapshot_hash == run["snapshot_hash"], "APPROVAL_BINDING", "Approval must bind the exact snapshot that was shown.")
            run["approval"] = {"snapshot_hash": snapshot_hash, "approved": approve, "operator": "local.institution", "ts": self.clock()}
            self.book.event(db, run_id, "HUMAN_APPROVED" if approve else "HUMAN_REJECTED", run["approval"], self.clock())
            if not approve:
                run.update(status="denied", decision={"verdict": "deny", "code": "HUMAN_REJECTED", "message": "The operator rejected this exact snapshot."})
            else:
                try: self._issue(db, run, approved=True)
                except SAACError as e:
                    run.update(status="denied", decision={"verdict": "deny", "code": e.code, "message": e.message})
                    self.book.event(db, run_id, "AUTHORITY_DENIED", run["decision"], self.clock())
            self.book.save_run(db, run)
        return run
