"""Reproducible end-to-end measurements; synthetic fixture, not production throughput."""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
import json
import platform
import time
from saac.swarm.campaign import Campaign

reports=[]
for scenario,population in [('fanout',100),('fanout',1200),('fanout',5000),('team',1000)]:
    with TemporaryDirectory() as d:
        started=time.monotonic();c=Campaign.create(d,scenario,population,scheduling='concurrent')
        setup=time.monotonic()-started
        started=time.monotonic();c.run();elapsed=time.monotonic()-started
        audit_start=time.monotonic();audit=c.service.audit();audit_time=time.monotonic()-audit_start
        s=c.summary()
        assert not s['error'] and audit['verification']['valid']
        reports.append({'scenario':scenario,'population':population,'workers':16,'setup_seconds':round(setup,3),
                        'run_seconds':round(elapsed,3),'audit_seconds':round(audit_time,3),'counts':s['counts'],
                        'institutional':s['institutional'],'risk':s['risk'], 'events':audit['verification']['events'],
                        'database_bytes':sum(p.stat().st_size for p in Path(d).glob('risk.sqlite*'))})
        print(json.dumps(reports[-1]),flush=True)
output={'date':datetime.now(timezone.utc).date().isoformat(),'python':platform.python_version(),'platform':platform.platform(),'measurements':reports,
        'caveat':'Single local measurements. Real signatures, reservations, socket effects and receipts; logical actors, no LLMs. Includes denial-heavy and mixed valid-cycle workload. Not a capacity guarantee.'}
destination=Path('.runtime/benchmarks/swarm.json')
destination.parent.mkdir(parents=True,exist_ok=True)
destination.write_text(json.dumps(output,indent=2)+'\n')
