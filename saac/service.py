"""Composition root for the trusted institution and its simulated socket."""
from pathlib import Path
import time
from .models import PolicyConfig, Proposal, require
from .crypto import Signer, digest
from .risk_book import RiskBook, decode
from .authority import Authority
from .protected_socket import ProtectedSocket
from .reconciliation import Reconciler
from .domain_models import parse_proposal, DomainPolicy, CancelProposal
from .profiles import AUDIENCES, PROFILE_OPERATIONS
from .accounting import dimensions


class SAACService:
    def __init__(self, directory, clock=None, profile='payments'):
        self.directory = Path(directory)
        self.profile = profile
        self.book = RiskBook(self.directory / "risk.sqlite")
        self.issuer = Signer(self.directory / "keys/riskbook.key", "riskbook-1")
        self.socket_signer = Signer(self.directory / "keys/socket.key", "socket-1")
        self.base_clock = clock or (lambda: int(time.time()))
        self.offset = 0
        with self.book.transaction() as db:
            if not db.execute("SELECT 1 FROM state").fetchone(): self._seed(db)
            self.offset = self.book.state(db).get("clock_offset", 0)
            self.profile = self.book.state(db).get('profile', 'payments')
        self.audience = AUDIENCES[self.profile]
        self.authority = Authority(self.book, self.issuer, self.now)
        self.socket = ProtectedSocket(self.book, self.issuer.public, self.socket_signer, self.now, self.audience)
        self.reconciler = Reconciler(self.book, self.socket_signer.public, self.now)

    def now(self): return self.base_clock()+self.offset

    def _pack(self, config, previous=None):
        return self.issuer.sign({**config.model_dump(), "typ": "saac-pack", "pack_id": f"{self.profile}.v{config.version}",
            "previous_hash": previous, "effective_at": self.now(), "owner": "institution.demo",
            "catalog": {"payment": "consequential transfer", "dispatch": "scoped child grant"} if self.profile == 'payments' else {op: 'consequential effect' for op in PROFILE_OPERATIONS[self.profile]}})

    def _seed(self, db):
        from .risk_book import encode
        if self.profile != 'payments':
            from .profiles import seed
            state, policy, scope = seed(self.profile)
            pack = self._pack(policy)
            state.update(pack=pack, pack_hash=digest(pack))
            self.book.save_state(db, state)
            db.execute('INSERT INTO packs VALUES (?,?)', (state['pack_hash'], encode(pack)))
            self.book.save_grant(db, {**scope, 'id': 'G-DEMO', 'principal_id': 'institution.demo', 'agent_id': 'agent.demo',
                'socket_id': AUDIENCES[self.profile], 'session_id': 'session.demo', 'exp': self.now()+86400*365,
                'parent_grant_id': None, 'depth': 0, 'version': 1, 'active': True, 'chain': []})
            self.book.event(db, None, 'INSTITUTION_INITIALIZED', {'pack': pack, 'pack_hash': state['pack_hash'], 'profile': self.profile}, self.now())
            return
        pack = self._pack(PolicyConfig())
        resources = {"beneficiary:acme": {"name": "Acme Research", "account": "SIM-1042", "version": 1, "sanctioned": False},
                     "beneficiary:community": {"name": "Community Lab", "account": "SIM-2088", "version": 1, "sanctioned": False},
                     "beneficiary:unknown": {"name": "Unlisted Recipient", "account": "SIM-9999", "version": 1, "sanctioned": True}}
        aliases = {"acme": "beneficiary:acme", "acme research": "beneficiary:acme", "vendor-001": "beneficiary:acme", "payroll": "beneficiary:acme",
                   "community": "beneficiary:community", "community lab": "beneficiary:community", "unknown": "beneficiary:unknown"}
        aliases.update({x: x for x in resources})
        state = {"pack": pack, "pack_hash": digest(pack), "resources": resources, "aliases": aliases,
                 "route_versions": {"local-ach": 1, "local-wire": 1}, "sanctions_version": 1, "breaker": False, "clock_offset": 0}
        self.book.save_state(db, state)
        db.execute("INSERT INTO packs VALUES (?,?)", (state["pack_hash"], encode(pack)))
        self.book.save_grant(db, {"id": "G-DEMO", "principal_id": "institution.demo", "agent_id": "agent.demo", "socket_id": "PAYMENTS-1",
            "session_id": "session.demo", "resources": ["beneficiary:acme", "beneficiary:community"], "routes": ["local-ach", "local-wire"],
            "currencies": ["USD"], "purposes": ["invoice", "research"], "operations": ["payment", "dispatch"], "max_amount_cents": 100000000,
            "exp": self.now()+86400*365, "parent_grant_id": None, "depth": 0, "version": 1, "active": True, "chain": []})
        self.book.event(db, None, "INSTITUTION_INITIALIZED", {"pack": pack, "pack_hash": state["pack_hash"]}, self.now())

    def get_run(self, run_id):
        with self.book.transaction() as db:
            run = self.book.run(db, run_id)
            require(run is not None, "UNKNOWN_PROPOSAL", "No proposal has this identifier.")
            return {**run, "events": self.book.events(db, run_id)}

    def view(self):
        with self.book.transaction() as db:
            state = self.book.state(db)
            runs = [decode(r[0]) for r in db.execute("SELECT data FROM runs ORDER BY rowid DESC LIMIT 150")]
            reservations = [{k: v for k,v in dict(r).items() if k != "kappa"} for r in db.execute("SELECT * FROM reservations")]
            return {"now": self.now(), "state": state, "risk": self.book.risk(db), "runs": runs, "reservations": reservations,
                "grants": [decode(r[0]) for r in db.execute("SELECT data FROM grants")],
                "payments": [dict(r) for r in db.execute("SELECT * FROM payments")],
                'profile': self.profile, 'audience': self.audience, 'metrics': dimensions(self.book.risk(db)),
                'orders': [decode(r[0]) for r in db.execute('SELECT data FROM orders')],
                'inbox': [decode(r[0]) for r in db.execute('SELECT data FROM inbox')],
                'jobs': [decode(r[0]) for r in db.execute('SELECT data FROM jobs')],
                'unsafe_sink': [decode(r[0]) for r in db.execute('SELECT data FROM unsafe_sink')],
                "keys": {"riskbook-1": self.issuer.public, "socket-1": self.socket_signer.public}}

    def execute(self, run_id, proposal=None, audience=None, mode="full", lose_receipt=False, pause_receipt=False, runner_fault=None):
        run = self.get_run(run_id)
        require("kappa" in run, "NO_CAPABILITY", "A proposal or verdict is not execution authority.")
        p = parse_proposal(proposal or run.get("execution_proposal", run["proposal"]))
        result = self.redeem(p, run["kappa"], audience=audience, mode=mode, runner_fault=runner_fault)
        if result["accepted"]:
            if lose_receipt: self.reconciler.uncertain(run["reservation_id"])
            elif not pause_receipt: self.reconciler.accept(result["receipt"])
        return {**self.get_run(run_id), "attempt": {k:v for k,v in result.items() if k != "receipt" or not lose_receipt}}

    def redeem(self, proposal, kappa, *, audience=None, mode="full", runner_fault=None):
        """Explicit rail boundary: possession of a run ID never substitutes for κ.

        Returns signed evidence without reconciling it. Audience/fault overrides
        are operator experiments and are not exposed by the actor rail API.
        """
        audience = audience or self.audience
        socket = self.socket if audience == self.audience else ProtectedSocket(self.book, self.issuer.public, self.socket_signer, self.now, audience)
        p = parse_proposal(proposal)
        result = socket.execute(kappa, p, mode)
        if result['accepted'] and p.operation == 'job.run' and runner_fault != 'before_dispatch':
            from .runner import LocalRunner
            try:
                result['receipt'] = LocalRunner(self).finish(result['execution']['id'], runner_fault)
                with self.book.connect() as db:
                    result['execution'] = decode(db.execute('SELECT data FROM executions WHERE id=?', (result['execution']['id'],)).fetchone()[0])
            except Exception:
                self.reconciler.uncertain(kappa['reservation']['id'])
                raise
        return result

    def reconcile(self, run_id, cancel=False):
        run = self.get_run(run_id)
        require("reservation_id" in run, "NO_RESERVATION", "This proposal has no reservation.")
        if self.profile == 'runtime' and run.get('execution') and run['proposal']['operation'] == 'job.run' and run['execution']['result']['status'] != 'non_use':
            from .runner import LocalRunner
            receipt = LocalRunner(self).finish(run['execution']['id'])
            self.reconciler.accept(receipt)
            return self.get_run(run_id)
        receipt = self.socket.evidence(run["reservation_id"], cancel)
        self.reconciler.accept(receipt)
        return self.get_run(run_id)

    def close_unused(self, run_id):
        run = self.get_run(run_id)
        require('reservation_id' in run, 'NO_RESERVATION', 'This action has no reservation.')
        if self.profile == 'runtime' and run.get('execution') and run['proposal']['operation'] == 'job.run':
            from .runner import LocalRunner
            receipt = LocalRunner(self).close_queued(run['execution']['id'])
        else:
            require(not run.get('execution'), 'EXECUTED', 'Executed effects need outcome evidence, not a non-use claim.')
            receipt = self.socket.evidence(run['reservation_id'])
        self.reconciler.accept(receipt)
        return self.get_run(run_id)

    def publish(self, config):
        from .risk_book import encode
        with self.book.transaction() as db:
            state = self.book.state(db)
            require(config.version > state["pack"]["version"], "PACK_ROLLBACK", "Pack versions must increase; superseded hashes cannot become live again.")
            risk = self.book.risk(db)
            if self.profile == 'payments':
                require(isinstance(config, PolicyConfig), 'POLICY_SCHEMA', 'Payment policy requires the payment schema.')
                limits = {'amount_cents': config.session_limit_cents}
            else:
                require(isinstance(config, DomainPolicy), 'POLICY_SCHEMA', 'This rail requires a domain policy.')
                limits = config.limits
                require(set(limits) == set(state['pack']['limits']) == set(config.per_action), 'BUDGET_SCHEMA', 'Budget units cannot change under existing reservations.')
                require(set(config.approval_above) <= set(limits), 'BUDGET_SCHEMA', 'Approval thresholds require a known unit.')
                require(all(type(v) is int and v >= 0 for values in (limits, config.per_action, config.approval_above) for v in values.values()), 'BUDGET_AMOUNT', 'Policy budgets must be nonnegative integers.')
                require(config.price_min_cents <= config.price_max_cents, 'PRICE_COLLAR', 'Price collar must be ordered.')
            require(all(limits[k] >= v['used']+v['reserved'] for k,v in dimensions(risk).items()), 'LIMIT_BELOW_EXPOSURE', 'Reconcile exposure before lowering a collar beneath it.')
            pack = self._pack(config, state["pack_hash"])
            state.update(pack=pack, pack_hash=digest(pack))
            db.execute("INSERT INTO packs VALUES (?,?)", (state["pack_hash"], encode(pack)))
            self.book.save_state(db, state)
            self.book.event(db, None, "PACK_PUBLISHED", {"pack": pack, "pack_hash": state["pack_hash"], "operator": "local.institution"}, self.now())
        return state

    def resource_update(self, resource="beneficiary:acme", version=None, alias_name=None, alias_target=None, route=None):
        from .resolver import alias
        with self.book.transaction() as db:
            state = self.book.state(db)
            require(resource in state["resources"], "UNKNOWN_RESOURCE", "Unknown canonical resource.")
            if alias_name:
                require(alias_target in state["resources"], "UNKNOWN_RESOURCE", "Alias must resolve to a known canonical resource.")
                state["aliases"][alias(alias_name)] = alias_target
            elif route:
                require(route in state["route_versions"], "UNKNOWN_ROUTE", "Unknown route.")
                state["route_versions"][route] += 1
            else:
                current = state["resources"][resource]["version"]
                require(version is None or version > current, "RESOURCE_ROLLBACK", "Resource versions must increase.")
                state["resources"][resource]["version"] = version or current+1
            self.book.save_state(db, state)
            self.book.event(db, None, "RESOLVER_UPDATED", {"resource": resource, "resources": state["resources"], "aliases": state["aliases"], "route_versions": state["route_versions"], "operator": "local.institution"}, self.now())
        return state

    def advance(self, seconds):
        require(type(seconds) is int and 0 < seconds <= 3600, "CLOCK", "Demo clock advances by 1–3600 seconds.")
        with self.book.transaction() as db:
            state = self.book.state(db)
            self.offset += seconds
            state["clock_offset"] = self.offset
            self.book.save_state(db, state)
            self.book.event(db, None, "DEMO_CLOCK_ADVANCED", {"seconds": seconds}, self.now())
        return self.now()

    def breaker(self, halted):
        with self.book.transaction() as db:
            state = self.book.state(db)
            state["breaker"] = halted
            self.book.save_state(db, state)
            grant = self.book.grant(db, "G-DEMO")
            grant["operations"] = [] if halted else (['payment', 'dispatch'] if self.profile == 'payments' else PROFILE_OPERATIONS[self.profile])
            grant["version"] += 1
            self.book.save_grant(db, grant)
            self.book.event(db, None, "GRANT_DEGRADED" if halted else "GRANT_INDEPENDENTLY_RESTORED", {"grant": grant, "operator": "local.institution"}, self.now())

    def fill(self, run_id, quantity=400, price_cents=None, lose_receipt=False):
        run = self.get_run(run_id)
        require(run.get('execution', {}).get('result', {}).get('order') is not None, 'UNKNOWN_ORDER', 'Submit an order first.')
        order = run['execution']['result']['order']
        receipt = self.socket.fill_order(order['id'], quantity, price_cents or order['limit_price_cents'])
        if lose_receipt: self.reconciler.uncertain(run['reservation_id'])
        else: self.reconciler.accept(receipt)
        return self.get_run(run_id)

    def cancel_order(self, run_id, lose_receipt=False):
        run = self.get_run(run_id)
        require(run.get('execution', {}).get('result', {}).get('order') is not None, 'UNKNOWN_ORDER', 'Submit an order first.')
        proposal = CancelProposal(order_id=run['execution']['result']['order']['id'], principal_id=run['proposal']['principal_id'],
                                  agent_id=run['proposal']['agent_id'], grant_id=run['proposal']['grant_id'])
        command = self.authority.propose(proposal)
        require('kappa' in command, command['decision']['code'], command['decision']['message'])
        command = self.execute(command['id'], lose_receipt=lose_receipt)
        if command['attempt']['accepted']:
            if lose_receipt: self.reconciler.uncertain(run['reservation_id'])
            else: self.reconcile(run_id)
        return {'run': self.get_run(run_id), 'cancel_run': command}

    def change_state(self, change):
        """Explicit, operator-only state mutations scoped to one experiment."""
        from .risk_book import encode
        with self.book.transaction() as db:
            state = self.book.state(db)
            if change == 'resource':
                key = next(iter(state['resources']))
                state['resources'][key]['version'] += 1
            elif change == 'route':
                require(bool(state['route_versions']), 'UNKNOWN_ROUTE', 'This rail has no route binding.')
                state['route_versions'][next(iter(state['route_versions']))] += 1
            elif change == 'consent':
                require(self.profile == 'referrals', 'PROFILE', 'Consent belongs to referrals.')
                consent = state['consent']['patient:P-104']
                consent.update(version=consent['version']+1, active=not consent['active'])
            elif change == 'document':
                require(self.profile == 'referrals', 'PROFILE', 'Document versions belong to referrals.')
                state['documents']['doc-1']['content'] += ' Updated synthetic version.'
                state['documents']['doc-1']['version'] += 1
            elif change == 'alias':
                require(self.profile == 'payments', 'PROFILE', 'This alias experiment uses payment beneficiaries.')
                state['aliases']['payroll'] = 'beneficiary:community'
            else:
                require(False, 'STATE_CHANGE', 'Unknown operator state change.')
            self.book.save_state(db, state)
            self.book.event(db, None, 'AUTHORITATIVE_STATE_CHANGED', {'change': change, 'operator': 'local.institution', 'state': state}, self.now())
        return self.view()


class ServiceTransport:
    """In-process wiring for the trusted operator's domain experiments only.

    Never give this object (or its service) to an untrusted Python process. Actor
    applications use sdk.HTTPTransport and the identity-bound HTTP boundary.
    """
    def __init__(self, service):
        self.service = service

    def authorize(self, proposal, preview=False, *, request_id=None):
        return self.service.authority.propose(parse_proposal(proposal), preview=preview, request_id=request_id)

    def execute(self, proposal, kappa):
        return self.service.redeem(proposal, kappa)

    def reconcile(self, receipt):
        return self.service.reconciler.accept(receipt)
