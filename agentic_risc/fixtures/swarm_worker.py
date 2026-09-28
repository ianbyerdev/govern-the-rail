"""Fixed benign child. Its stdout pipe is mapped to identity by the supervisor."""
import json
import pathlib
import socket

probes=[]
for path,write in [('/institution/risk.sqlite',False),('/operator.token',False),('/outside.txt',True)]:
    try:
        if write: pathlib.Path(path).write_text('synthetic')
        else: pathlib.Path(path).read_text()
        probes.append({'path':path,'blocked':False})
    except OSError: probes.append({'path':path,'blocked':True})
try:
    with socket.create_connection(('127.0.0.1',8000),timeout=.2): pass
    probes.append({'path':'host-loopback:8000','blocked':False})
except OSError: probes.append({'path':'host-loopback:8000','blocked':True})
print(json.dumps({'probes':probes,'requests':[
    {'operation':'artifact.read'},
    {'operation':'artifact.read','agent_id':'actor-0000'},
    {'operation':'egress.publish','resource':'endpoint:public','credential':'mock-public-key'},
]}))
