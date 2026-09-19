"""Small real process proof. No arbitrary code, external network, or host fallback."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from ..authority import live_grant
from ..crypto import digest
from ..protocol import verify_artifact as verify
from ..domain_models import parse_proposal
from ..risk_book import decode, encode, uid
from ..models import require, SAACError
from ..runner import executable_contract, available
from .models import ChildScope

SCRIPT=Path(__file__).parents[1]/'fixtures/swarm_worker.py'

def contract():
    return {'script_sha256':hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),'executables':executable_contract(),
            'containment':'bubblewrap-unshare-all','network':'none','channel':'supervisor-owned stdout pipe','version':1}

class WorkerRunner:
    def __init__(self,svc): self.svc=svc

    def close_queued(self,run_id):
        svc=self.svc;run=svc.get_run(run_id)
        child_id=run['kappa']['effect']['x']['child_id']
        with svc.book.transaction() as db:
            child=decode(db.execute('SELECT data FROM swarm_children WHERE id=?',(child_id,)).fetchone()[0])
            require(child['status']=='queued','OUTCOME_UNCERTAIN','Only an unclaimed worker intent has provable non-use.')
            row=db.execute('SELECT * FROM reservations WHERE id=?',(run['reservation_id'],)).fetchone()
            execution=decode(db.execute('SELECT data FROM executions WHERE id=?',(run['execution']['id'],)).fetchone()[0])
            child.update(status='cancelled',version=child['version']+1)
            db.execute('UPDATE swarm_children SET data=? WHERE id=?',(encode(child),child_id))
            grant=svc.book.grant(db,child['grant_id']);grant.update(active=False,version=grant['version']+1);svc.book.save_grant(db,grant)
            execution['revision']+=1
            bound=run['kappa']['reservation']['bound']
            execution['result']={'status':'non_use','child':child,'consumed':{k:0 for k in bound},'released':bound}
            db.execute("UPDATE nonces SET status='closed' WHERE nonce=?",(run['kappa']['nonce'],))
            svc.book.event(db,run_id,'WORKER_NON_USE_PROVEN',{'child_id':child_id,'launch_count':0},svc.now())
            return svc.sockets['SUPERVISOR-1']._persist(db,row,execution,svc.now())

    def finish(self,run_id):
        svc=self.svc;run=svc.get_run(run_id);k=run['kappa'];child_id=k['effect']['x']['child_id']
        folder=svc.directory/'workers'/child_id;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        journal=folder/'exit.json';signer=svc.sockets['SUPERVISOR-1'].signer
        if journal.exists():
            evidence=decode(journal.read_text());verify(evidence,signer.public,signer.kid,'saac-worker-exit')
            require(evidence['execution_id']==run['execution']['id'],'WORKER_BINDING','Exit evidence belongs to another execution.')
        else:
            require(available(),'CONTAINMENT_UNAVAILABLE','Bubblewrap and system Python are required; there is no host fallback.')
            with svc.book.transaction() as db:
                child=decode(db.execute('SELECT data FROM swarm_children WHERE id=?',(child_id,)).fetchone()[0])
                require(child['status']=='queued','OUTCOME_UNCERTAIN','A claimed worker cannot be relaunched without exit evidence.')
                state=svc.book.state(db);now=svc.now()
                verify(k,svc.issuer.public,'riskbook-1','saac-kappa')
                verify(state['pack'],svc.issuer.public,'riskbook-1','saac-pack')
                require(digest(state['pack'])==state['pack_hash']==k['pack_hash'],'PACK_NOT_LIVE','Dispatch pack changed before the worker launch claim.')
                require(k['iat']<=now<k['exp'],'EXPIRED','Dispatch expired before launch.')
                require(not state['breaker'],'BREAKER_HALT','Institution halted queued dispatch.')
                live_grant(svc.book,db,k['grant_id'],now)
                require(state['resources'][k['effect']['r']]['version']==k['effect']['c']['resource']['version'],'STALE_STATE','Workspace changed before launch.')
                require(k['effect']['c']['runtime']==contract(),'RUNTIME_STALE','The contained program or runner changed.')
                nonce=db.execute('SELECT * FROM nonces WHERE nonce=?',(k['nonce'],)).fetchone()
                require(nonce['status']=='consumed' and nonce['execution_id']==run['execution']['id'],'WORKER_BINDING','Launch must follow the single redeemed dispatch.')
                child.update(status='claimed',launch_count=1)
                db.execute('UPDATE swarm_children SET data=? WHERE id=?',(encode(child),child_id))
                grant=svc.book.grant(db,child['grant_id']);grant['active']=True;svc.book.save_grant(db,grant)
                svc.book.event(db,run_id,'WORKER_LAUNCH_CLAIMED',{'child':child_id,'launch_count':1,'program_hash':k['effect']['x']['program_hash']},now)
            sealed=folder/'worker.py';data=SCRIPT.read_bytes()
            require(hashlib.sha256(data).hexdigest()==k['effect']['c']['runtime']['script_sha256'],'RUNTIME_STALE','Program changed while sealing it.')
            sealed.write_bytes(data)
            args=[shutil.which('bwrap'),'--unshare-all','--die-with-parent','--new-session','--clearenv',
                  '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--proc','/proc','--dev','/dev',
                  '--tmpfs','/tmp','--dir','/task','--ro-bind',str(sealed.resolve()),'/task/worker.py','--chdir','/tmp','--remount-ro','/',
                  '/usr/bin/python3','-I','/task/worker.py']
            try:
                output=subprocess.run(args,capture_output=True,text=True,timeout=5,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
                result=json.loads(output.stdout) if output.returncode==0 else {'probes':[],'requests':[]}
                require(len(output.stdout)<20000 and len(result['requests'])<=3,'WORKER_OUTPUT','Worker output exceeded the fixed protocol.')
                responses=[]
                for i,request in enumerate(result['requests']):
                    try:
                        proposed=svc.propose_as(child_id,request,key=f'worker:{child_id}:{i}')
                        if 'kappa' in proposed: proposed=svc.execute(proposed['id'])
                        responses.append({'run_id':proposed['id'],'code':proposed['decision']['code'],'status':proposed['status']})
                    except SAACError as err: responses.append({'code':err.code,'message':err.message})
                evidence={'execution_id':run['execution']['id'],'child_id':child_id,'returncode':output.returncode,
                          'probes':result['probes'],'responses':responses,'stderr':output.stderr[:2000], 'launch_count':1,
                          'channel_binding':'Supervisor supplied child identity; child JSON cannot choose identity.'}
            except subprocess.TimeoutExpired:
                evidence={'execution_id':run['execution']['id'],'child_id':child_id,'returncode':-9,'launch_count':1,'error':'Timeout; subprocess killed and reaped.'}
            evidence=signer.sign({'typ':'saac-worker-exit',**evidence})
            temp=folder/'exit.tmp'
            with temp.open('w') as f: f.write(encode(evidence));f.flush();os.fsync(f.fileno())
            os.replace(temp,journal)
            fd=os.open(folder,os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        with svc.book.transaction() as db:
            row=db.execute('SELECT * FROM reservations WHERE id=?',(run['reservation_id'],)).fetchone()
            execution=decode(db.execute('SELECT data FROM executions WHERE id=?',(run['execution']['id'],)).fetchone()[0])
            if execution['result']['status']=='completed':
                return decode(db.execute('SELECT data FROM receipts WHERE reservation_id=? ORDER BY revision DESC LIMIT 1',(row['id'],)).fetchone()[0])
            child=decode(db.execute('SELECT data FROM swarm_children WHERE id=?',(child_id,)).fetchone()[0])
            child.update(status='stopped',version=child['version']+1)
            db.execute('UPDATE swarm_children SET data=? WHERE id=?',(encode(child),child_id))
            grant=svc.book.grant(db,child['grant_id']);grant.update(active=False,version=grant['version']+1);svc.book.save_grant(db,grant)
            execution['revision']+=1;execution['result'].update(status='completed',child=child,evidence=evidence)
            execution['result']['consumed']['agent_starts']=1
            execution['result']['released']['agent_slots']=1
            svc.book.event(db,run_id,'WORKER_EXIT_OBSERVED',evidence,svc.now())
            return svc.sockets['SUPERVISOR-1']._persist(db,row,execution,svc.now())


def contained_proof(svc,count=2):
    require(type(count)is int and 2<=count<=8,'WORKERS','This proof uses 2–8 fixed processes.')
    def one(_):
        child='child-'+uid('proof')
        run=svc.authority.propose({'operation':'agent.spawn','program':'contained-proof','child_id':child,'scope':ChildScope(exp=svc.now()+300).model_dump()})
        if 'kappa' not in run: return {'run_id':run['id'],'decision':run['decision']}
        run=svc.execute(run['id'])
        return {'run_id':run['id'],'evidence':run.get('receipt',{}).get('result',{}).get('evidence'),'status':run['status']}
    with ThreadPoolExecutor(max_workers=count) as pool: workers=list(pool.map(one,range(count)))
    result={'requested_processes':count,'workers':workers,'program_contract':contract(),
            'claim':'Actual OS containment plus supervisor-bound proposal channels. This is separate from the logical population.'}
    return result
