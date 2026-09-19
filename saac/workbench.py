"""Operator-scoped experiment workspaces; all effects use the common SAAC core."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
import re
from threading import Barrier, Lock
from typing import Literal
from .service import SAACService, ServiceTransport
from .sdk import SAACClient
from .adapters import propose_via
from .models import StrictModel, PolicyConfig, Proposal, require
from .domain_models import DomainPolicy, parse_proposal, DelegateProposal, JobScope
from .profiles import AUDIENCES, default_proposal
WORKBENCH_PROFILES = [p for p in AUDIENCES if p != "swarm"]
from .risk_book import uid, encode
from .accounting import dimensions
from .tape import verify_tape


class CreateBook(StrictModel):
    profile: str = 'payments'


class ProposeRequest(StrictModel):
    proposal: dict
    preview: bool = False
    integration: Literal['native', 'harness', 'mcp'] = 'native'
    request_id: str | None = None


class DemoRequest(StrictModel):
    integration: Literal['native', 'harness', 'mcp'] = 'native'


class RaceRequest(StrictModel):
    mode: Literal['saac', 'naive'] = 'saac'


class ExecuteRequest(StrictModel):
    proposal: dict | None = None
    audience: str | None = None
    lose_receipt: bool = False
    pause_receipt: bool = True
    runner_fault: str | None = None


class FillRequest(StrictModel):
    quantity: int = 400
    price_cents: int | None = None
    lose_receipt: bool = False


class ChangeRequest(StrictModel):
    change: str


class ProbeRequest(StrictModel):
    probe: str


class ExperimentStore:
    def __init__(self, directory, main, demo=None):
        self.demo = demo
        self.directory, self.main = Path(directory) / 'experiments', main
        self.directory.mkdir(exist_ok=True)
        self.lock = Lock()

    def get(self, book_id):
        if book_id == 'main': return self.main
        require(bool(re.fullmatch(r'book_[a-f0-9]{16}', book_id)), 'BOOK_ID', 'Invalid experiment identifier.')
        folder = self.directory / book_id
        require((folder / 'risk.sqlite').exists(), 'BOOK_ID', 'This experiment does not exist.')
        return SAACService(folder)

    def create(self, profile):
        require(profile in WORKBENCH_PROFILES, 'PROFILE', 'Unknown domain profile.')
        with self.lock:
            if self.demo and len(list(self.directory.glob('book_*'))) >= self.demo.manager.limits.books:
                from .public_demo import refuse
                refuse('DEMO_BOOK_LIMIT', 'This demo has reached its experiment limit. Export your evidence and end the demo.')
            book_id = uid('book')
            svc = SAACService(self.directory / book_id, profile=profile)
        return book_id, svc

    def listing(self):
        books = [{'id': 'main', 'profile': 'payments', 'label': 'Original payment session'}]
        for folder in sorted(self.directory.glob('book_*'), key=lambda p:p.stat().st_mtime, reverse=True):
            if (folder / 'risk.sqlite').exists():
                svc = self.get(folder.name)
                books.append({'id': folder.name, 'profile': svc.profile, 'label': f'{svc.profile} · {folder.name[-6:]}'})
        return books


def bundle(book_id, svc, run=None, **extra):
    return {'book_id': book_id, 'view': svc.view(), 'run': svc.get_run(run['id']) if run else None, **extra}


def race(store, profile, mode='saac'):
    require(mode in ('saac', 'naive'), 'RACE_MODE', 'Choose saac or naive.')
    book_id, svc = store.create(profile)
    if profile == 'payments':
        proposal = Proposal(amount_cents=10_000_000, ttl_seconds=300)
        svc.publish(PolicyConfig(version=2, session_limit_cents=100_000_000, per_action_cents=10_000_000, approval_above_cents=10_000_000))
    else:
        proposal = default_proposal(profile)
        config = DomainPolicy.model_validate({k:v for k,v in svc.view()['state']['pack'].items() if k in DomainPolicy.model_fields})
        if profile == 'trading':
            proposal = proposal.model_copy(update={'quantity': 1})
            limits = {'notional_usd_cents': proposal.limit_price_cents*10}
        elif profile == 'referrals':
            proposal = proposal.model_copy(update={'documents': ['doc-1']})
            limits = {'records': 10, 'bytes': 650}
        else: limits = {'job_starts': 10, 'job_slots': 10}
        svc.publish(config.model_copy(update={'version': 2, 'limits': limits, 'approval_above': limits}))
    # Operator bootstraps distinct identities, all bound to ONE existing session.
    # Neither an adapter nor an actor can create books or alter this manifest.
    from copy import deepcopy
    with svc.book.transaction() as db:
        parent = svc.book.grant(db, 'G-DEMO')
        for i in range(100):
            grant = {**deepcopy(parent), 'id': f'G-RACE-{i}', 'agent_id': f'agent.race.{i}',
                     'parent_grant_id': parent['id'], 'parent_version': parent['version'],
                     'chain': [parent['id']], 'depth': 1}
            svc.book.save_grant(db, grant)
        svc.book.event(db, None, 'CONCURRENCY_EXPERIMENT', {'actors': 100, 'mode': mode,
            'shared_session': parent['session_id'], 'proposal': proposal.model_dump(), 'risk': svc.book.risk(db)}, svc.now())
    barrier = Barrier(100)
    def issue(i):
        barrier.wait(timeout=30)
        return svc.authority.propose(proposal.model_copy(update={'agent_id': f'agent.race.{i}', 'grant_id': f'G-RACE-{i}'}))
    runs = []
    if mode == 'saac':
        with ThreadPoolExecutor(max_workers=100) as pool: runs = list(pool.map(issue, range(100)))
    winners = [r for r in runs if 'kappa' in r]
    # Separate intentionally unsafe check-then-act model: no signer, socket or ledger.
    unsafe_barrier, lock = Barrier(100), Lock()
    unsafe = {'reserved': 0, 'accepted': 0}
    def naive(_):
        free = 10-unsafe['reserved']
        unsafe_barrier.wait(timeout=30)
        if free >= 1:
            with lock:
                unsafe['reserved'] += 1
                unsafe['accepted'] += 1
    with ThreadPoolExecutor(max_workers=100) as pool: list(pool.map(naive, range(100)))
    if profile == 'payments':
        unsafe.update(exposure_cents=unsafe['accepted']*proposal.amount_cents, limit_cents=100_000_000)
    with svc.book.transaction() as db:
        svc.book.event(db, None, 'NAIVE_POLICY_COMPARISON', {'unsafe': unsafe, 'signed_capabilities': 0,
            'protected_executions': 0, 'explanation': '100 threads check stale capacity before the barrier; isolated arithmetic only.'}, svc.now())
        events = svc.book.events(db)
    report = verify_tape(events, svc.view()['keys'])
    return bundle(book_id, svc, winners[0] if winners else None, race={'mode': mode, 'accepted': len(winners), 'denied': 100-len(winners) if runs else 0,
        'requests': [{'run_id': r['id'], 'agent_id': r['proposal']['agent_id'], 'code': r['decision']['code']} for r in runs],
        'outcomes': ['kappa' in r for r in runs], 'unsafe': unsafe, 'verification': report})


def probe(svc, name):
    if name == 'unsigned':
        result = svc.socket.execute({}, Proposal() if svc.profile == 'payments' else default_proposal(svc.profile))
        return {'mechanism': 'Protected socket', 'blocked': not result['accepted'], 'result': result}
    require(svc.profile == 'runtime', 'PROFILE', 'The containment and delegation lab belongs to the runtime profile.')
    if name in ('delegate', 'expand'):
        scope = JobScope(exp=svc.now()+60, network=['external'] if name == 'expand' else [])
        run = svc.authority.propose(DelegateProposal(delegation=scope))
        if 'kappa' in run: run = svc.execute(run['id'])
        return {'mechanism': 'Monotonic grant scope', 'blocked': 'kappa' not in run, 'run': run}
    require(name == 'unprotected', 'PROBE', 'Unknown fixed coverage probe.')
    from .coverage import unprotected_write
    entry = unprotected_write(svc.directory/'coverage')
    with svc.book.transaction() as db:
        db.execute('INSERT INTO unsafe_sink VALUES (?,?)', (entry['id'], encode(entry)))
        svc.book.event(db, None, 'UNPROTECTED_COUNTEREXAMPLE', entry, svc.now())
    return {'mechanism': 'No enforcement — isolated dummy sink', 'blocked': False, 'result': entry}


def register(app, directory, main, operator, ApprovalRequest, ClockRequest, BreakerRequest, *,
             prefix='/api/operator/workbench', store_dependency=None):
    from fastapi import Depends, HTTPException
    from pydantic import ValidationError
    from .runner import available
    default_store = ExperimentStore(directory, main) if store_dependency is None else None
    select_store = store_dependency or (lambda: default_store)

    @app.get(prefix, dependencies=[Depends(operator)])
    def catalog(store=Depends(select_store)):
        return {'books': store.listing(), 'profiles': WORKBENCH_PROFILES, 'containment_available': available() and store.demo is None,
                'public_demo': bool(store.demo),
                'defaults': {'payments': Proposal().model_dump(), **{p: default_proposal(p).model_dump() for p in WORKBENCH_PROFILES if p != 'payments'}}}

    @app.post(prefix+'/books', dependencies=[Depends(operator)])
    def create(request: CreateBook, store=Depends(select_store)):
        book_id, svc = store.create(request.profile)
        return bundle(book_id, svc)

    @app.get(prefix+'/books/{book_id}', dependencies=[Depends(operator)])
    def view(book_id: str, store=Depends(select_store)): return bundle(book_id, store.get(book_id))

    @app.post(prefix+'/books/{book_id}/proposals', dependencies=[Depends(operator)])
    def propose(book_id: str, request: ProposeRequest, store=Depends(select_store)):
        svc = store.get(book_id)
        try: proposal = parse_proposal(request.proposal)
        except ValidationError as error: raise HTTPException(422, str(error)) from error
        return bundle(book_id, svc, propose_via(request.integration, SAACClient(ServiceTransport(svc)), proposal.model_dump(), request.preview, request_id=request.request_id))

    @app.get(prefix+'/books/{book_id}/runs/{run_id}', dependencies=[Depends(operator)])
    def run(book_id: str, run_id: str, store=Depends(select_store)):
        svc = store.get(book_id)
        return bundle(book_id, svc, svc.get_run(run_id))

    @app.post(prefix+'/books/{book_id}/runs/{run_id}/execute', dependencies=[Depends(operator)])
    def execute(book_id: str, run_id: str, request: ExecuteRequest, store=Depends(select_store)):
        svc = store.get(book_id)
        if store.demo and svc.profile == 'runtime':
            raise HTTPException(403, 'Real process execution is available only in the administrator workspace. Public demos can inspect job authority and delegation.')
        require(request.runner_fault in (None, 'before_dispatch', 'after_claim', 'after_run'), 'RUNNER_FAULT', 'Unknown controlled runner fault.')
        try: result = svc.execute(run_id, **request.model_dump())
        except ValidationError as error: raise HTTPException(422, str(error)) from error
        return bundle(book_id, svc, result)

    @app.post(prefix+'/books/{book_id}/runs/{run_id}/approval', dependencies=[Depends(operator)])
    def approve(book_id: str, run_id: str, request: ApprovalRequest, store=Depends(select_store)):
        svc = store.get(book_id)
        return bundle(book_id, svc, svc.authority.approve(run_id, **request.model_dump()))

    @app.post(prefix+'/books/{book_id}/runs/{run_id}/reconcile', dependencies=[Depends(operator)])
    def reconcile(book_id: str, run_id: str, store=Depends(select_store)):
        svc = store.get(book_id)
        return bundle(book_id, svc, svc.reconcile(run_id))

    @app.post(prefix+'/books/{book_id}/runs/{run_id}/close-unused', dependencies=[Depends(operator)])
    def close_unused(book_id: str, run_id: str, store=Depends(select_store)):
        svc = store.get(book_id)
        return bundle(book_id, svc, svc.close_unused(run_id))

    @app.post(prefix+'/books/{book_id}/runs/{run_id}/fill', dependencies=[Depends(operator)])
    def fill(book_id: str, run_id: str, request: FillRequest, store=Depends(select_store)):
        svc = store.get(book_id)
        return bundle(book_id, svc, svc.fill(run_id, **request.model_dump()))

    @app.post(prefix+'/books/{book_id}/runs/{run_id}/cancel', dependencies=[Depends(operator)])
    def cancel(book_id: str, run_id: str, store=Depends(select_store)):
        svc = store.get(book_id)
        result = svc.cancel_order(run_id)
        return bundle(book_id, svc, result['run'], cancel_run=result['cancel_run'])

    @app.put(prefix+'/books/{book_id}/pack', dependencies=[Depends(operator)])
    def pack(book_id: str, request: dict, store=Depends(select_store)):
        svc = store.get(book_id)
        try: config = (PolicyConfig if svc.profile == 'payments' else DomainPolicy).model_validate(request)
        except ValidationError as error: raise HTTPException(422, str(error)) from error
        svc.publish(config)
        return bundle(book_id, svc)

    @app.post(prefix+'/books/{book_id}/state', dependencies=[Depends(operator)])
    def change(book_id: str, request: ChangeRequest, store=Depends(select_store)):
        svc = store.get(book_id); svc.change_state(request.change)
        return bundle(book_id, svc)

    @app.post(prefix+'/books/{book_id}/clock', dependencies=[Depends(operator)])
    def advance(book_id: str, request: ClockRequest, store=Depends(select_store)):
        svc = store.get(book_id); svc.advance(request.seconds)
        return bundle(book_id, svc)

    @app.post(prefix+'/books/{book_id}/breaker', dependencies=[Depends(operator)])
    def breaker(book_id: str, request: BreakerRequest, store=Depends(select_store)):
        svc = store.get(book_id); svc.breaker(request.halted)
        return bundle(book_id, svc)

    @app.get(prefix+'/books/{book_id}/tape', dependencies=[Depends(operator)])
    def tape(book_id: str, store=Depends(select_store)):
        svc = store.get(book_id)
        with store.demo.heavy(budget=False) if store.demo else nullcontext():
            with svc.book.transaction() as db: events = svc.book.events(db)
            keys = svc.view()['keys']
            return {'events': events, 'keys': keys, 'verification': verify_tape(events, keys)}

    @app.post(prefix+'/race/{profile}', dependencies=[Depends(operator)])
    def concurrent(profile: str, request: RaceRequest, store=Depends(select_store)):
        with store.demo.heavy() if store.demo else nullcontext():
            return race(store, profile, request.mode)

    @app.post(prefix+'/demonstrations/{scenario}', dependencies=[Depends(operator)])
    def demonstrate(scenario: str, request: DemoRequest, store=Depends(select_store)):
        from .demonstrations import demonstrate as run_demo
        return run_demo(store, scenario, request.integration)

    @app.post(prefix+'/books/{book_id}/probe', dependencies=[Depends(operator)])
    def probes(book_id: str, request: ProbeRequest, store=Depends(select_store)):
        svc = store.get(book_id); result = probe(svc, request.probe)
        return bundle(book_id, svc, result.get('run'), probe=result)
