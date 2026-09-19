"""Negative vectors, positive controls, and per-commit conservation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from saac.api import create_app
from saac.crypto import digest
from saac.domain_models import DomainPolicy
from saac.models import SAACError
from saac.risk_book import decode, encode
from saac.swarm.campaign import Campaign
from saac.swarm.service import SwarmService
from saac.swarm.models import ChildScope
from saac.swarm.workers import contained_proof

@pytest.fixture
def svc(tmp_path):
    s=SwarmService(tmp_path,clock=lambda:1800000000);s.bootstrap(2)
    return s

def proposal(s,operation='artifact.write',**kwargs):
    return s.propose_as('actor-0000',{'operation':operation,**kwargs})

def budget(s,key):
    with s.book.connect() as db: return s.book.risk(db)['budgets'][key]

def spawn(s,child='child-demo',actor='actor-0000',scope=None):
    return s.propose_as(actor,{'operation':'agent.spawn','child_id':child,'scope':scope or ChildScope(exp=s.now()+300).model_dump()})

def publish(s,**changes):
    with s.book.connect() as db: old=s.book.state(db)['pack']
    p=DomainPolicy.model_validate({k:v for k,v in old.items() if k in DomainPolicy.model_fields})
    s.publish(p.model_copy(update={'version':p.version+1,**changes}))


def test_complete_lifecycle_and_preview_not_authority(svc):
    preview=svc.propose_as('actor-0000',{'operation':'artifact.write'},preview=True)
    assert preview['decision']['verdict']=='allow' and 'kappa' not in preview
    with pytest.raises(SAACError,match='A proposal or verdict'): svc.execute(preview['id'])
    r=proposal(svc,actor_risk_observed={'agent_slots':-100000,'messages':0})
    assert budget(svc,'messages')['reserved']==1
    out=svc.execute(r['id'],pause_receipt=True)
    assert out['attempt']['accepted'] and budget(svc,'messages')['reserved']==1
    svc.reconcile(r['id'])
    assert budget(svc,'messages')['used']==1 and budget(svc,'messages')['reserved']==0
    assert svc.audit()['verification']['valid']

@pytest.mark.parametrize('change,expected',[
 ({'payload':'more bytes and changed meaning'},'EFFECT_MISMATCH'),
 ({'resource':'artifact:team-b'},'STALE_STATE'),
 ({'recipient':'team-b'},'EFFECT_MISMATCH'),
 ({'principal_id':'other'},'IDENTITY_BINDING'),
])
def test_exact_effect(svc,change,expected):
    r=proposal(svc)
    out=svc.execute(r['id'],proposal={**r['proposal'],**change})
    assert out['attempt']['code']==expected
    assert budget(svc,'messages')['used']==0
    assert svc.execute(r['id'])['attempt']['accepted']


def test_replay_expiry_audience(svc):
    r=proposal(svc)
    assert svc.execute(r['id'],audience='RESOURCE-1')['attempt']['code']=='WRONG_AUDIENCE'
    assert svc.execute(r['id'])['attempt']['accepted']
    assert svc.execute(r['id'])['attempt']['code']=='REPLAY'
    r=proposal(svc,ttl_seconds=1);svc.advance(2)
    assert svc.execute(r['id'])['attempt']['code']=='EXPIRED'
    assert budget(svc,'messages')['reserved']==1
    svc.reconcile(r['id']);assert budget(svc,'messages')['reserved']==0


def test_aliases_and_read_commitment(svc):
    r=proposal(svc,resource='team-results');assert r['snapshot']['effect']['r']=='artifact:team-a'
    assert svc.execute(r['id'],proposal={**r['proposal'],'resource':'artifact:team-a'})['attempt']['accepted']
    read=proposal(svc,'artifact.read')
    later=proposal(svc,payload='NEW SYNTHETIC MESSAGE');svc.execute(later['id'])
    assert svc.execute(read['id'])['attempt']['code']=='EFFECT_MISMATCH'
    new=proposal(svc,'artifact.read');out=svc.execute(new['id'])
    assert 'NEW SYNTHETIC MESSAGE' in out['execution']['result']['data']
    r=proposal(svc,resource='team-results')
    svc.resource_update(resource='artifact:team-a',alias_name='team-results',alias_target='artifact:team-b')
    assert svc.execute(r['id'])['attempt']['code']=='STALE_STATE'

@pytest.mark.parametrize('change',['resource','route','pack'])
def test_live_bindings(svc,change):
    r=proposal(svc,'egress.publish')
    if change=='pack': publish(svc)
    elif change=='route': svc.resource_update(resource='endpoint:lab',route='local-only')
    else: svc.resource_update(resource='endpoint:lab')
    assert svc.execute(r['id'])['attempt']['code']==('PACK_NOT_LIVE' if change=='pack' else 'STALE_STATE')
    assert budget(svc,'disclosed_bytes')['reserved']>0

@pytest.mark.parametrize('scenario,reason',[
 ('simulation','RESOURCE_DENIED'),('credential','RESOURCE_DENIED'),('coordination','RESOURCE_DENIED'),
 ('expansion','DELEGATION_EXPANSION'),('pivot','WRONG_AUDIENCE'),('upload','RESOURCE_DENIED'),('revocation','PACK_NOT_LIVE')])
def test_domain_negative_vectors(tmp_path,scenario,reason):
    c=Campaign.create(tmp_path,scenario,2);c.run()
    assert not c.summary()['error']
    assert c.summary()['reasons']=={reason:2}
    assert c.service.audit()['verification']['valid']


def test_snapshot_approval_does_not_authorize_mutation(svc):
    publish(svc,approval_above={'disclosed_bytes':1})
    r=proposal(svc);assert r['status']=='awaiting_approval'
    approved=svc.authority.approve(r['id'],r['snapshot_hash'],True)
    assert svc.execute(r['id'],proposal={**r['proposal'],'payload':'different'})['attempt']['code']=='EFFECT_MISMATCH'
    assert svc.execute(approved['id'])['attempt']['accepted']
    r=proposal(svc);svc.resource_update(resource='artifact:team-a')
    assert svc.authority.approve(r['id'],r['snapshot_hash'],True)['decision']['code']=='STALE_STATE'

@pytest.mark.parametrize('field,value',[
 ('resources',['resource:private']),('operations',['shell.any']),('audiences',['OTHER']),
 ('purposes',['anything']),('recipients',['public']),('principal_id','other'),('exp',1900000000),
 ('ceilings',{'messages':99,'read_bytes':4096,'disclosed_bytes':4096,'agent_starts':1,'agent_slots':1})])
def test_delegation_subset_all_dimensions(svc,field,value):
    scope=ChildScope(exp=svc.now()+300).model_dump();scope[field]=value
    r=spawn(svc,scope=scope)
    assert 'kappa' not in r and r['decision']['code'].startswith('DELEGATION')


def test_child_shared_budget_live_ancestry_and_stop(svc):
    run=spawn(svc);svc.execute(run['id'])
    assert budget(svc,'agent_starts')['used']==1 and budget(svc,'agent_slots')['reserved']==1
    child=svc.propose_as('child-demo',{'operation':'artifact.read'})
    assert svc.execute(child['id'])['attempt']['accepted']
    with svc.book.connect() as db:
        parent=svc.book.grant(db,'G-actor-0000');derived=svc.book.grant(db,'G-child-demo')
    assert parent['session_id']==derived['session_id']
    with svc.book.transaction() as db:
        parent['version']+=1;svc.book.save_grant(db,parent)
    assert svc.propose_as('child-demo',{'operation':'artifact.read'})['decision']['code']=='GRANT_STALE'
    assert budget(svc,'agent_slots')['reserved']==1 # revocation does not stop work
    stop=svc.authority.propose({'operation':'agent.stop','child_id':'child-demo'})
    svc.execute(stop['id'],lose_receipt=True)
    assert budget(svc,'agent_slots')['reserved']==1 # local effect alone cannot release book
    svc.reconcile(stop['id'])
    assert budget(svc,'agent_slots')['reserved']==0 and budget(svc,'agent_starts')['used']==1
    assert svc.audit()['verification']['valid']


def test_receipt_loss_restart_and_wrong_observer(svc):
    r=proposal(svc,'egress.publish');out=svc.execute(r['id'],lose_receipt=True)
    held=budget(svc,'disclosed_bytes')['reserved'];assert held>0
    svc.advance(300);assert budget(svc,'disclosed_bytes')['reserved']==held
    with svc.book.connect() as db:
        rho=decode(db.execute('SELECT data FROM receipts WHERE reservation_id=?',(r['reservation_id'],)).fetchone()[0])
    forged=svc.sockets['ARTIFACT-1'].signer.sign({k:v for k,v in rho.items() if k not in ('sig','kid')})
    with pytest.raises(SAACError,match='valid signature'): svc.reconciler.accept(forged)
    restored=SwarmService(svc.directory,clock=lambda:1800000000)
    restored.reconcile(r['id']);assert budget(restored,'disclosed_bytes')['used']==held
    assert not restored.reconciler.accept(rho)['applied']
    assert budget(restored,'disclosed_bytes')['used']==held


def test_forged_journal_and_transcript_do_not_release(svc):
    r=spawn(svc);svc.execute(r['id'],lose_receipt=True)
    with svc.book.connect() as db:
        rho=decode(db.execute('SELECT data FROM receipts WHERE reservation_id=?',(r['reservation_id'],)).fetchone()[0])
    rho['result']['released']['agent_slots']=1
    forged=svc.sockets['SUPERVISOR-1'].signer.sign({k:v for k,v in rho.items() if k not in ('sig','kid')})
    with pytest.raises(SAACError,match='journal'): svc.reconciler.accept(forged)
    assert budget(svc,'agent_slots')['reserved']==1
    assert proposal(svc,actor_risk_observed={'agent_slots':0})['status']=='authorized'
    assert budget(svc,'agent_slots')['reserved']==1


def test_100_simultaneous_distinct_identities_and_per_commit_audit(tmp_path):
    svc=SwarmService(tmp_path,clock=lambda:1800000000);svc.bootstrap(100)
    barrier=Barrier(100)
    def issue(i):
        barrier.wait(timeout=20)
        return spawn(svc,child=f'child-{i}',actor=f'actor-{i:04d}')
    with ThreadPoolExecutor(max_workers=100) as pool: runs=list(pool.map(issue,range(100)))
    assert sum('kappa' in r for r in runs)==64
    assert budget(svc,'agent_slots')['reserved']==64
    audit=svc.audit();assert audit['verification']['valid'] and audit['ledger_projection_verified']
    for event in audit['events']:
        if event['kind']=='RISK_RESERVED':
            d=event['data'];assert all(v['used']+v['reserved']+d['bound'][k]<=v['limit'] for k,v in d['risk_before']['budgets'].items())

@pytest.mark.parametrize('population',[1200,5000])
def test_large_population_shared_collar(tmp_path,population):
    c=Campaign.create(tmp_path,'fanout',population,scheduling='concurrent');c.run();s=c.summary()
    assert not s['error'];assert s['counts']=={'executed':64,'denied':population-64}
    assert s['risk']['budgets']['agent_slots']['reserved']==64
    assert c.service.audit()['verification']['valid']


def test_lifetime_starts_not_refunded_by_stops(svc):
    with svc.book.connect() as db: limits=svc.book.state(db)['pack']['limits']
    publish(svc,limits={**limits,'agent_starts':2,'agent_slots':1})
    for i in range(2):
        r=spawn(svc,child=f'child-{i}');svc.execute(r['id'])
        stop=svc.authority.propose({'operation':'agent.stop','child_id':f'child-{i}'});svc.execute(stop['id'])
    assert budget(svc,'agent_slots')['reserved']==0
    assert spawn(svc,child='child-last')['decision']['code']=='SESSION_LIMIT'


def test_coverage_and_bad_policy_are_counterexamples(tmp_path):
    for posture in ('full','partial','baseline'):
        c=Campaign.create(tmp_path/posture,'coverage',4,posture=posture);c.run();s=c.summary()
        assert s['oracle']['bypasses']==(0 if posture=='full' else 4)
        assert s['institutional']['capabilities']==0
        assert s['risk']['budgets']['disclosed_bytes']['used']==0
        assert c.service.audit()['verification']['valid']
    c=Campaign.create(tmp_path/'bad','bad-policy',2);c.run()
    assert c.summary()['counts']=={'executed':2} and c.summary()['risk']['budgets']['disclosed_bytes']['used']>0


def test_reactive_prerequisites_change_population_outcomes(tmp_path):
    c=Campaign.create(tmp_path,'coordination',4,scheduling='reactive');c.run()
    assert c.summary()['counts']=={'denied':2,'skipped':2}


def test_restart_after_issuance_and_execution_does_not_duplicate(tmp_path):
    c=Campaign.create(tmp_path,'team',2)
    item=c.intent(0)['data'];svc=c.service
    r=svc.propose_as(item['actor'],item['proposal'],key=item['intent_id'])
    c=Campaign(tmp_path);c.run(1)
    assert c.intent(0)['run_id']==r['id']
    with c.service.book.transaction() as db: db.execute("UPDATE swarm_intents SET status='pending' WHERE seq=0")
    c=Campaign(tmp_path);c.run(1)
    assert budget(c.service,'messages')['used']==1
    with c.service.book.connect() as db: assert db.execute('SELECT COUNT(*) FROM swarm_messages').fetchone()[0]==1
    with pytest.raises(SAACError,match='changed canonical effect'):
        c.service.propose_as('actor-0000',{'operation':'artifact.write','payload':'tamper'},key=item['intent_id'])


def test_actual_contained_process_channels(svc):
    result=contained_proof(svc,2)
    for worker in result['workers']:
        e=worker['evidence'];assert e['returncode']==0 and e['launch_count']==1
        assert all(p['blocked'] for p in e['probes'])
        assert e['responses'][0]['status']=='settled'
        assert e['responses'][1]['code']=='IDENTITY_BINDING'
        assert e['responses'][2]['code'] in ('GRANT_BINDING','GRANT_SCOPE')
        svc.reconcile(worker['run_id']) # signed supervisor journal restart recovery
    assert budget(svc,'agent_starts')['used']==2 and budget(svc,'agent_slots')['reserved']==0
    assert svc.audit()['verification']['valid']


def test_http_identity_operator_boundary_pagination_compare(tmp_path):
    app=create_app(tmp_path);client=TestClient(app)
    op={'Authorization':'Bearer '+app.state.operator_token}
    actor={'Authorization':'Bearer '+app.state.actor_token}
    prefix='/api/operator/swarm'
    assert client.post(prefix,json={'population':2},headers=actor).status_code==403
    c=client.post(prefix,json={'population':2,'scenario':'team'},headers=op).json();cid=c['id']
    token=client.post(prefix+'/'+cid+'/channels',json={'actor':'actor-0000'},headers=op).json()['token']
    ch={'Authorization':'Bearer '+token}
    p='/api/swarm/'+cid+'/proposals'
    assert client.post(p,json={'request_id':'x','proposal':{'operation':'artifact.read','agent_id':'actor-0001'}},headers=ch).json()['code']=='IDENTITY_BINDING'
    r=client.post(p,json={'request_id':'x','proposal':{'operation':'artifact.read'}},headers=ch).json()
    assert client.post('/api/swarm/'+cid+'/runs/'+r['id']+'/execute',json={},headers=ch).json()['status']=='settled'
    assert client.post(prefix+'/'+cid+'/control',json={'action':'revoke'},headers=ch).status_code==401
    c2=client.post(prefix+'/'+cid+'/compare',json={'posture':'baseline'},headers=op).json()
    assert c2['intent_manifest_hash']==c['intent_manifest_hash']
    assert client.post('/api/swarm/'+c2['id']+'/proposals',json={'request_id':'x','proposal':{'operation':'artifact.read'}},headers=ch).status_code==401
    first=client.get(prefix+'/'+cid+'/events?limit=2',headers=op).json()
    second=client.get(prefix+'/'+cid+'/events?after='+str(first['next_cursor'])+'&limit=2',headers=op).json()
    assert first['items'][-1]['seq']<second['items'][0]['seq']
    exported=client.get(prefix+'/'+cid+'/export',headers=op).json()
    assert exported['verification']['valid'] and token not in json.dumps(exported)


def test_published_negative_vector_manifest(tmp_path):
    vectors=json.loads(Path('tests/fixtures/swarm-negative-vectors.json').read_text())
    for v in vectors['vectors']:
        c=Campaign.create(tmp_path/v['scenario'],v['scenario'],1);c.run()
        assert c.intent(0)['outcome']['code']==v['expected']


def test_workbench_catalog_preserves_four_single_rail_profiles(tmp_path):
    app=create_app(tmp_path);client=TestClient(app)
    op={'Authorization':'Bearer '+app.state.operator_token}
    response=client.get('/api/operator/workbench',headers=op)
    assert response.status_code==200
    assert set(response.json()['defaults'])=={'payments','trading','referrals','runtime'}


def test_contained_queued_non_use_and_ambiguous_launch(svc,monkeypatch):
    from saac.swarm.workers import WorkerRunner
    from saac.domain_models import parse_proposal
    from saac.swarm import workers
    scope=ChildScope(exp=svc.now()+300).model_dump()
    r=svc.authority.propose({'operation':'agent.spawn','child_id':'child-queued','scope':scope,'program':'contained-proof'})
    result=svc.sockets['SUPERVISOR-1'].execute(r['kappa'],parse_proposal(r['proposal']))
    assert result['accepted']
    assert budget(svc,'agent_starts')['reserved']==1
    svc.close_unused(r['id'])
    assert budget(svc,'agent_starts')['reserved']==0 and budget(svc,'agent_starts')['used']==0
    with pytest.raises(SAACError): WorkerRunner(svc).finish(r['id'])
    r=svc.authority.propose({'operation':'agent.spawn','child_id':'child-ambiguous','scope':scope,'program':'contained-proof'})
    svc.sockets['SUPERVISOR-1'].execute(r['kappa'],parse_proposal(r['proposal']))
    with svc.book.transaction() as db:
        row=decode(db.execute("SELECT data FROM swarm_children WHERE id='child-ambiguous'").fetchone()[0]);row['status']='claimed'
        db.execute("UPDATE swarm_children SET data=? WHERE id='child-ambiguous'",(encode(row),))
    svc.reconciler.uncertain(r['reservation_id'])
    with pytest.raises(SAACError,match='relaunched'): WorkerRunner(svc).finish(r['id'])
    with pytest.raises(SAACError,match='unclaimed'): svc.close_unused(r['id'])
    assert budget(svc,'agent_starts')['reserved']==1 and budget(svc,'agent_slots')['reserved']==1


def test_containment_unavailable_never_falls_back(svc,monkeypatch):
    from saac.swarm import workers
    monkeypatch.setattr(workers,'available',lambda:False)
    r=svc.authority.propose({'operation':'agent.spawn','child_id':'child-no-sandbox','scope':ChildScope(exp=svc.now()+300).model_dump(),'program':'contained-proof'})
    with pytest.raises(SAACError,match='no host fallback'): svc.execute(r['id'])
    assert svc.get_run(r['id'])['status']=='uncertain'
    svc.close_unused(r['id'])
    assert budget(svc,'agent_slots')['reserved']==0


def test_staged_intent_survives_tamper_then_scheduler_executes_once(tmp_path):
    c=Campaign.create(tmp_path,'team',2)
    item=c.stage(0);assert item['status']=='authorized'
    assert c.summary()['processed']==0 and budget(c.service,'messages')['reserved']==1
    r=item['run']
    assert c.service.execute(r['id'],proposal={**r['proposal'],'payload':'changed'})['attempt']['code']=='EFFECT_MISMATCH'
    c.run();assert c.summary()['counts']=={'executed':2}
    assert budget(c.service,'messages')['used']==1
    assert c.service.execute(r['id'])['attempt']['code']=='REPLAY'


def test_virtual_clock_comparison_and_restart(tmp_path):
    c=Campaign.create(tmp_path/'a','team',2,authority_epoch=1800000000)
    now=c.service.now();c.service.advance(301)
    restored=Campaign(tmp_path/'a');assert restored.service.now()==now+301
    with c.service.book.connect() as db: manifest=[decode(r[0]) for r in db.execute('SELECT data FROM swarm_intents ORDER BY seq')]
    compared=Campaign.create(tmp_path/'b','team',2,manifest=manifest,authority_epoch=now)
    assert compared.meta()['intent_manifest_hash']==c.meta()['intent_manifest_hash']
    assert compared.service.now()==now
    assert compared.service.audit()['verification']['valid']


def test_non_use_recovery_is_not_counted_as_an_execution(tmp_path):
    c=Campaign.create(tmp_path,'team',2)
    staged=c.stage(0);c.service.close_unused(staged['run_id'])
    c.run(1)
    assert c.intent(0)['status']=='closed'
    assert c.summary()['institutional']['redemptions']==0
    assert budget(c.service,'messages')['used']==0
    assert c.service.audit()['verification']['valid']
