"""One campaign, multiple independently verifying sockets, registered actor channels."""
from copy import deepcopy
from ..service import SAACService
from ..crypto import Signer, digest
from ..risk_book import encode, decode
from ..models import require
from ..domain_models import parse_proposal
from ..protected_socket import ProtectedSocket
from ..reconciliation import Reconciler
from .models import SOCKETS

class SwarmService(SAACService):
    def __init__(self, directory, clock=None):
        super().__init__(directory, clock, 'swarm')
        with self.book.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS swarm_channels(token_hash TEXT PRIMARY KEY, actor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS swarm_actors(id TEXT PRIMARY KEY, grant_id TEXT UNIQUE NOT NULL, origin TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS swarm_children(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS swarm_messages(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS swarm_disclosures(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS swarm_meta(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS swarm_intents(seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, actor TEXT NOT NULL,
                    data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', run_id TEXT, outcome TEXT);
                CREATE INDEX IF NOT EXISTS swarm_pending ON swarm_intents(status,seq);
                CREATE TABLE IF NOT EXISTS swarm_oracle(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            ''')
        self.sockets, self.registry = {}, {}
        for aud in sorted(set(SOCKETS.values())):
            signer = Signer(self.directory / f'keys/{aud}.key', 'observer-'+aud)
            self.registry[aud] = {'kid': signer.kid, 'public': signer.public}
            self.sockets[aud] = ProtectedSocket(self.book, self.issuer.public, signer, self.now, aud)
        self.reconciler = Reconciler(self.book, None, self.now, self.registry)

    def bootstrap(self, count):
        require(1 <= count <= 5000, 'POPULATION', 'Choose 1–5,000 logical actors.')
        with self.book.transaction() as db:
            require(not db.execute('SELECT 1 FROM swarm_actors').fetchone(), 'BOOTSTRAP_ONCE', 'Only an independent new experiment can bootstrap peers.')
            root = self.book.grant(db, 'G-DEMO')
            for i in range(count):
                aid = f'actor-{i:04d}'
                grant = {**deepcopy(root), 'id': 'G-'+aid, 'agent_id': aid, 'parent_grant_id': root['id'], 'parent_version': root['version'], 'depth': 1, 'chain': [root['id']]}
                self.book.save_grant(db, grant)
                db.execute('INSERT INTO swarm_actors VALUES (?,?,?)', (aid, grant['id'], 'bootstrap-peer'))
            self.book.event(db, None, 'CAMPAIGN_BOOTSTRAPPED', {'logical_actors': count, 'shared_session': root['session_id'], 'origin': 'independent operator manifest; peers are not spawned children'}, self.now())

    def identity(self, actor):
        with self.book.connect() as db:
            row = db.execute('SELECT * FROM swarm_actors WHERE id=?', (actor,)).fetchone()
            require(row is not None, 'ACTOR_CHANNEL', 'No trusted transport binding exists for this actor.')
            grant = self.book.grant(db, row['grant_id'])
            return {'agent_id': actor, 'grant_id': grant['id'], 'principal_id': grant['principal_id']}

    def propose_as(self, actor, proposal, key=None, preview=False):
        # actor comes from supervisor-owned channel / credential registry, never request JSON.
        identity = self.identity(actor)
        for k,v in identity.items(): require(k not in proposal or proposal[k] == v, 'IDENTITY_BINDING', 'An actor cannot impersonate a peer or replace its grant.')
        return self.authority.propose(parse_proposal({**proposal, **identity}), preview=preview, idempotency_key=key)

    def execute(self, run_id, proposal=None, audience=None, lose_receipt=False, pause_receipt=False, **ignored):
        run = self.get_run(run_id)
        require('kappa' in run, 'NO_CAPABILITY', 'A proposal or verdict is not execution authority.')
        aud = audience or run['kappa']['aud']
        require(aud in self.sockets, 'WRONG_AUDIENCE', 'No protected socket is registered for this audience.')
        result = self.sockets[aud].execute(run['kappa'], parse_proposal(proposal or run['proposal']))
        if result['accepted']:
            if run['proposal']['operation']=='agent.spawn' and run['proposal'].get('program')=='contained-proof':
                from .workers import WorkerRunner
                try:
                    result['receipt']=WorkerRunner(self).finish(run_id)
                    result['execution']=self.get_run(run_id)['execution']
                except Exception:
                    self.reconciler.uncertain(run['reservation_id'])
                    raise
            if lose_receipt: self.reconciler.uncertain(run['reservation_id'])
            elif not pause_receipt:
                self.reconciler.accept(result['receipt'])
                if run['proposal']['operation'] == 'agent.stop':
                    child = result['execution']['result']['child']
                    with self.book.connect() as db:
                        row = db.execute('SELECT reservation_id FROM executions WHERE id=?', (child['execution_id'],)).fetchone()
                    self.reconciler.accept(self.sockets['SUPERVISOR-1'].evidence(row[0]))
        return {**self.get_run(run_id), 'attempt': {k:v for k,v in result.items() if k != 'receipt' or not lose_receipt}}

    def reconcile(self, run_id, cancel=False):
        run = self.get_run(run_id)
        require('kappa' in run, 'NO_CAPABILITY', 'No authority was issued.')
        if run['proposal']['operation']=='agent.spawn' and run['proposal'].get('program')=='contained-proof' and run.get('execution') and run['execution']['result']['status']!='non_use':
            from .workers import WorkerRunner
            receipt=WorkerRunner(self).finish(run_id)
        else:
            receipt=self.sockets[run['kappa']['aud']].evidence(run['reservation_id'])
        self.reconciler.accept(receipt)
        if run['proposal']['operation'] == 'agent.stop' and run.get('execution'):
            child = run['execution']['result']['child']
            with self.book.connect() as db:
                rid = db.execute('SELECT reservation_id FROM executions WHERE id=?', (child['execution_id'],)).fetchone()[0]
            self.reconciler.accept(self.sockets['SUPERVISOR-1'].evidence(rid))
        return self.get_run(run_id)

    def close_unused(self,run_id):
        run=self.get_run(run_id)
        require('kappa' in run,'NO_CAPABILITY','No authority was issued.')
        if run.get('execution'):
            require(run['proposal']['operation']=='agent.spawn' and run['proposal'].get('program')=='contained-proof',
                    'EXECUTED','An executed effect cannot be declared unused.')
            from .workers import WorkerRunner
            receipt=WorkerRunner(self).close_queued(run_id)
        else:
            receipt=self.sockets[run['kappa']['aud']].evidence(run['reservation_id'])
        self.reconciler.accept(receipt)
        return self.get_run(run_id)

    def keys(self):
        return {'riskbook-1': self.issuer.public, '__audiences__': {a:v['kid'] for a,v in self.registry.items()}, **{v['kid']:v['public'] for v in self.registry.values()}}

    def audit(self):
        from ..tape import verify_tape
        with self.book.connect() as db:
            events = self.book.events(db)
            risk = self.book.risk(db)
            meta=db.execute('SELECT data FROM swarm_meta').fetchone()
            if meta:
                manifest=[decode(r[0]) for r in db.execute('SELECT data FROM swarm_intents ORDER BY seq')]
                expected=decode(meta[0])['intent_manifest_hash']
                recorded=[e['data']['intent_manifest_hash'] for e in events if e['kind']=='EXPERIMENT_MANIFEST']
                require(digest(manifest)==expected and recorded==[expected],'MANIFEST_INTEGRITY','The replay manifest differs from its committed event.')
            totals = {k: {'used':0, 'reserved':0} for k in risk['budgets']}
            for row in db.execute('SELECT data FROM allocations'):
                for k,v in decode(row[0]).items():
                    totals[k]['used'] += v['consumed']; totals[k]['reserved'] += v['bound']-v['consumed']-v['released']
            require(all(totals[k] == {n:v[n] for n in ('used','reserved')} for k,v in risk['budgets'].items()), 'PROJECTION_INTEGRITY', 'Cached totals differ from the allocation ledger.')
        return {'events': events, 'keys': self.keys(), 'verification': verify_tape(events, self.keys()), 'ledger_projection_verified': True}
