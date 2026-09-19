from typing import Literal
import hashlib
import secrets
from contextlib import nullcontext
from fastapi import Depends, Header, HTTPException, Query
from pydantic import Field, ValidationError
from ..models import StrictModel, require
from ..risk_book import encode, decode
from .catalog import CATALOG
from .campaign import CampaignStore

class CreateCampaign(StrictModel):
    scenario: str = 'fanout'
    population: int = Field(default=100, ge=1, le=5000)
    posture: Literal['full','partial','baseline'] = 'full'
    scheduling: Literal['replay','reactive','concurrent'] = 'replay'

class Control(StrictModel):
    action: Literal['play','pause','step','recover','stop','revoke','restore','pack','close-unused']

class Compare(StrictModel):
    posture: Literal['full','partial','baseline']

class MintChannel(StrictModel):
    actor: str

class ActorIntent(StrictModel):
    proposal: dict
    request_id: str = Field(min_length=1,max_length=100)

class ExecuteIntent(StrictModel):
    proposal: dict | None = None
    audience: str | None = None

class Probe(StrictModel):
    action: Literal['stage','replay','tamper','audience','stale','expiry']

class WorkerProof(StrictModel):
    count: int = Field(default=2,ge=2,le=8)


def register(app, directory, operator, *, prefix='/api/operator/swarm', store_dependency=None):
    default_store=CampaignStore(directory) if store_dependency is None else None
    if default_store: app.state.campaign_store=default_store
    select_store=store_dependency or (lambda: default_store)

    @app.get(prefix, dependencies=[Depends(operator)])
    def catalog(store=Depends(select_store)):
        books=[]
        for p in sorted(store.directory.glob('campaign_*/risk.sqlite'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
            c=store.get(p.parent.name); books.append(c.meta())
        return {**CATALOG,'campaigns':books, 'max_population':store.demo.manager.limits.population if store.demo else 5000, 'contained_workers':store.demo is None}

    @app.post(prefix, dependencies=[Depends(operator)])
    def create(request:CreateCampaign, store=Depends(select_store)): return store.create(**request.model_dump()).summary()

    @app.get(prefix+'/{cid}', dependencies=[Depends(operator)])
    def summary(cid:str, store=Depends(select_store)): return store.get(cid).summary()

    @app.get(prefix+'/{cid}/actors', dependencies=[Depends(operator)])
    def actors(cid:str, after:int=-1, limit:int=Query(default=250,ge=1,le=5000), store=Depends(select_store)): return store.get(cid).actors(after,limit)

    @app.get(prefix+'/{cid}/population', dependencies=[Depends(operator)])
    def population(cid:str, store=Depends(select_store)):
        c=store.get(cid)
        with c.service.book.connect() as db:
            states=''.join({'pending':'p','authorized':'a','closed':'c','executed':'e','denied':'d','bypassed':'b','skipped':'s'}[r[0]] for r in db.execute('SELECT status FROM swarm_intents ORDER BY seq'))
        return {'states':states,'encoding':{'p':'pending','a':'authorized','c':'closed','e':'executed','d':'denied','b':'bypassed','s':'skipped'}}

    @app.get(prefix+'/{cid}/intents/{seq}', dependencies=[Depends(operator)])
    def intent(cid:str,seq:int, store=Depends(select_store)): return store.get(cid).intent(seq)

    @app.post(prefix+'/{cid}/intents/{seq}/probe', dependencies=[Depends(operator)])
    def probe(cid:str,seq:int,request:Probe, store=Depends(select_store)):
        c=store.get(cid);svc=c.service
        require(not c.thread or not c.thread.is_alive(),'RUNNING','Pause the scheduler before changing a selected action.')
        if request.action=='stage': return c.stage(seq)
        item=c.intent(seq);run=item['run']
        require(run is not None and 'kappa' in run,'NO_CAPABILITY','Stage authority for the selected intent first.')
        proposal=dict(run['proposal']);audience=None
        if request.action=='tamper':
            if 'payload' in proposal: proposal['payload']+=' modified after issuance'
            elif 'child_id' in proposal: proposal['child_id']='child-substitution'
            else: proposal['max_bytes']=min(4096,proposal['max_bytes']+1)
        elif request.action=='audience': audience='RESOURCE-1' if run['kappa']['aud']!='RESOURCE-1' else 'ARTIFACT-1'
        elif request.action=='expiry': svc.advance(301)
        elif request.action=='stale': svc.resource_update(resource=run['kappa']['effect']['r'])
        result=svc.execute(run['id'],proposal=proposal,audience=audience)
        if result['attempt']['accepted']:
            c.finish(seq,run['id'],'executed',{'code':'EXECUTED','message':'The socket verified and executed the exact effect.'})
        return c.intent(seq)

    @app.get(prefix+'/{cid}/events', dependencies=[Depends(operator)])
    def events(cid:str,after:int=0,limit:int=Query(default=100,ge=1,le=250), store=Depends(select_store)):
        svc=store.get(cid).service
        with svc.book.connect() as db:
            rows=db.execute('SELECT seq,run_id,kind,ts FROM events WHERE seq>? ORDER BY seq LIMIT ?', (after,limit)).fetchall()
        items=[dict(r) for r in rows]
        return {'items':items,'next_cursor':items[-1]['seq'] if items else after}

    @app.get(prefix+'/{cid}/events/{seq}', dependencies=[Depends(operator)])
    def event(cid:str,seq:int, store=Depends(select_store)):
        with store.get(cid).service.book.connect() as db: row=db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()
        require(row is not None,'EVENT','Unknown event.')
        return {**dict(row),'data':decode(row['data'])}

    @app.post(prefix+'/{cid}/control', dependencies=[Depends(operator)])
    def control(cid:str,request:Control, store=Depends(select_store)):
        c=store.get(cid);svc=c.service
        if request.action in ('play','step'):
            if store.demo: store.demo.start_campaign(c, 1 if request.action=='step' else None)
            else: c.start(1 if request.action=='step' else None)
        elif request.action=='pause': c.stop.set()
        else:
            require(not c.thread or not c.thread.is_alive(),'RUNNING','Pause the scheduler before an institutional intervention.')
            if request.action=='recover': c.recover_receipts()
            elif request.action=='stop': c.stop_children()
            elif request.action in ('revoke','restore'): svc.breaker(request.action=='revoke')
            elif request.action=='pack':
                from ..domain_models import DomainPolicy
                with svc.book.connect() as db: pack=svc.book.state(db)['pack']
                config=DomainPolicy.model_validate({k:v for k,v in pack.items() if k in DomainPolicy.model_fields})
                svc.publish(config.model_copy(update={'version':config.version+1}))
            elif request.action=='close-unused':
                with svc.book.connect() as db:
                    rows=db.execute("SELECT r.run_id FROM reservations r JOIN nonces n ON n.nonce=r.nonce WHERE n.status='unused'").fetchall()
                    queued=db.execute("SELECT r.run_id FROM reservations r JOIN executions e ON r.id=e.reservation_id JOIN swarm_children c ON json_extract(c.data,'$.execution_id')=e.id WHERE json_extract(c.data,'$.status')='queued'").fetchall()
                for row in [*rows,*queued]:
                    svc.close_unused(row[0])
                    with svc.book.connect() as db: intent=db.execute('SELECT seq FROM swarm_intents WHERE run_id=?',(row[0],)).fetchone()
                    if intent: c.finish(intent[0],row[0],'closed',{'code':'NON_USE_PROVEN','message':'The unused authority was closed; no effect occurred.'})
        return c.summary()

    @app.post(prefix+'/{cid}/compare', dependencies=[Depends(operator)])
    def compare(cid:str,request:Compare, store=Depends(select_store)):
        source=store.get(cid);m=source.meta()
        with source.service.book.connect() as db:
            manifest=[decode(r[0]) for r in db.execute('SELECT data FROM swarm_intents ORDER BY seq')]
        target=store.create(scenario=m['scenario'],population=m['population'],posture=request.posture,scheduling=m['scheduling'],manifest=manifest,authority_epoch=m.get('authority_epoch',m['created_at']))
        target.update(comparison_of=cid)
        return target.summary()

    @app.get(prefix+'/{cid}/export', dependencies=[Depends(operator)])
    def export(cid:str, store=Depends(select_store)):
        c=store.get(cid)
        with store.demo.heavy(budget=False) if store.demo else nullcontext():
            with c.service.book.connect() as db:
                intents=[decode(r[0]) for r in db.execute('SELECT data FROM swarm_intents ORDER BY seq')]
                oracle=[decode(r[0]) for r in db.execute('SELECT data FROM swarm_oracle')]
            return {'contract':'SAAC','wire_version':CATALOG['wire_version'],'summary':c.summary(),'manifest':intents,'oracle_only':oracle,'catalog':CATALOG,**c.service.audit()}

    @app.post(prefix+'/{cid}/workers', dependencies=[Depends(operator)])
    def workers(cid:str,request:WorkerProof, store=Depends(select_store)):
        if store.demo: raise HTTPException(403, 'Real contained workers are available only in the administrator workspace.')
        from .workers import contained_proof
        return contained_proof(store.get(cid).service,request.count)

    if store_dependency is not None: return
    store=default_store

    @app.post(prefix+'/{cid}/channels', dependencies=[Depends(operator)])
    def channel(cid:str,request:MintChannel, store=Depends(select_store)):
        svc=store.get(cid).service;svc.identity(request.actor)
        token=secrets.token_urlsafe(32)
        with svc.book.transaction() as db:
            db.execute('INSERT INTO swarm_channels VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),request.actor))
        return {'actor':request.actor,'token':token,'scope':'Only this registered actor within this campaign; no institution control APIs.'}

    def channel_actor(cid:str,authorization:str=Header(default='')):
        svc=store.get(cid).service
        token=authorization.removeprefix('Bearer ')
        with svc.book.connect() as db:
            row=db.execute('SELECT actor FROM swarm_channels WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row: raise HTTPException(401,'A campaign-bound actor channel is required.')
        return row[0]

    @app.post('/api/swarm/{cid}/proposals')
    def actor_propose(cid:str,request:ActorIntent,actor=Depends(channel_actor)):
        try:
            run=store.get(cid).service.propose_as(actor,request.proposal,key='channel:'+actor+':'+request.request_id)
        except ValidationError as err:
            raise HTTPException(422, str(err)) from err
        return {k:v for k,v in run.items() if k not in ('risk_before','risk_after')}

    @app.post('/api/swarm/{cid}/runs/{rid}/execute')
    def actor_execute(cid:str,rid:str,request:ExecuteIntent,actor=Depends(channel_actor)):
        svc=store.get(cid).service;run=svc.get_run(rid)
        require(run['proposal']['agent_id']==actor,'IDENTITY_BINDING','This channel cannot redeem another actor’s authority.')
        try:
            result=svc.execute(rid,proposal=request.proposal,audience=request.audience)
        except ValidationError as err:
            raise HTTPException(422, str(err)) from err
        return {k:v for k,v in result.items() if k not in ('events','risk_before','risk_after')}
