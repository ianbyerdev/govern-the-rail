from copy import deepcopy
from functools import lru_cache
from ..crypto import digest
from ..domain_models import DomainPolicy
from ..models import Effect, SAACError, require
from ..accounting import admission
from ..risk_book import decode
from .models import OPERATIONS, SOCKETS, BUDGETS, PROPOSALS

PROGRAM = digest({'program': 'deterministic-research-worker', 'version': 1, 'network': 'none'})

@lru_cache(maxsize=1)
def schema_hash():
    return digest({o: m.model_json_schema() for o,m in PROPOSALS.items()})

def seed():
    resources = {
        'artifact:team-a': {'tenant': 'institution.demo', 'namespace': 'team-a', 'environment': 'lab', 'content': 'SYNTHETIC team-a result.', 'classification': 'team-private'},
        'artifact:team-b': {'tenant': 'institution.demo', 'namespace': 'team-b', 'environment': 'lab', 'content': 'SYNTHETIC other-task note.', 'classification': 'team-private'},
        'endpoint:lab': {'tenant': 'institution.demo', 'namespace': 'team-a', 'environment': 'lab', 'origin': 'mock://lab', 'path': '/publish', 'route': 'local-only', 'classification': 'team-private'},
        'endpoint:public': {'tenant': 'external.mock', 'namespace': 'public', 'environment': 'external-role', 'origin': 'mock://public', 'path': '/publish', 'route': 'local-only', 'classification': 'public'},
        'resource:lab': {'tenant': 'institution.demo', 'namespace': 'team-a', 'environment': 'lab', 'content': 'SYNTHETIC permitted data.', 'classification': 'team-private'},
        'resource:private': {'tenant': 'other.mock', 'namespace': 'private', 'environment': 'external-role', 'content': 'SYNTHETIC inaccessible data.', 'classification': 'restricted'},
        'workspace:team-a': {'tenant': 'institution.demo', 'namespace': 'team-a', 'environment': 'lab', 'classification': 'team-private'},
    }
    for r in resources.values(): r['version'] = 1
    allowed = ['artifact:team-a', 'endpoint:lab', 'resource:lab', 'workspace:team-a']
    ceilings = {'messages': 1, 'read_bytes': 4096, 'disclosed_bytes': 4096, 'agent_starts': 1, 'agent_slots': 1}
    policy = DomainPolicy(limits=BUDGETS, per_action=ceilings, resources=allowed, routes=['local-only'], purposes=['research'], recipients=['team-a'])
    state = {'profile': 'swarm', 'resources': resources, 'aliases': {**{r:r for r in resources}, 'shared-cache': 'artifact:team-b', 'team-results': 'artifact:team-a', 'simulation-target': 'endpoint:public'},
             'route_versions': {'local-only': 1}, 'credentials': {'mock-lab-key': {'version': 1, 'resources': ['endpoint:lab', 'resource:lab']},
             'mock-found-key': {'version': 1, 'resources': ['resource:private']}, 'mock-public-key': {'version':1, 'resources':['endpoint:public']}}, 'clock_offset': 0, 'breaker': False}
    grant = {'resources': allowed, 'operations': OPERATIONS, 'audiences': sorted(set(SOCKETS.values())), 'ceilings': ceilings,
             'purposes': ['research'], 'recipients': ['team-a'], 'routes': ['local-only'], 'network': []}
    return state, policy, grant


def resolve(p, state, grant, db):
    require(state.get('profile') == 'swarm', 'PROFILE_BINDING', 'This effect belongs to a campaign book.')
    from ..resolver import alias
    r = state['aliases'].get(alias(p.resource))
    require(r in state['resources'], 'UNKNOWN_RESOURCE', 'The trusted resolver knows only registered local resources.')
    resource = state['resources'][r]
    c = {'profile': 'swarm', 'pack_hash': state['pack_hash'], 'grant_version': grant['version'], 'grant_id': grant['id'],
         'schema_hash': schema_hash(),
         'resource': {k:v for k,v in resource.items() if k != 'content'}, 'resource_content_hash': digest(resource.get('content', '')),
         'campaign': grant['session_id']}
    x = {'purpose': getattr(p, 'purpose', 'research')}
    if p.operation.startswith('artifact.'):
        require(r.startswith('artifact:'), 'RESOURCE_TYPE', 'Artifact socket requires an artifact resource.')
        x.update(recipient=p.recipient, max_bytes=p.max_bytes)
        if p.operation == 'artifact.write': x.update(payload=p.payload, payload_hash=digest(p.payload), bytes=len(p.payload.encode()))
        else:
            messages = [decode(row[0])['payload'] for row in db.execute("SELECT data FROM swarm_messages WHERE json_extract(data,'$.resource')=? ORDER BY rowid", (r,))]
            data = ('\n'.join([resource['content'], *messages])).encode()[:p.max_bytes].decode(errors='ignore')
            x.update(bytes=len(data.encode()), data=data, read_hash=digest(data))
    elif p.operation in ('egress.publish', 'resource.access'):
        require(r.startswith('endpoint:' if p.operation == 'egress.publish' else 'resource:'), 'RESOURCE_TYPE', 'Socket and resource type differ.')
        cred = state['credentials'].get(p.credential)
        require(cred is not None, 'CREDENTIAL_UNKNOWN', 'Only synthetic credential references are supported.')
        c.update(credential_version=cred['version'], credential_resources=cred['resources'], route_version=state['route_versions']['local-only'])
        x.update(credential_ref=p.credential, route='local-only', method='POST' if p.operation == 'egress.publish' else 'GET')
        if p.operation == 'egress.publish':
            x.update(origin=resource['origin'], path=resource['path'], payload=p.payload, payload_hash=digest(p.payload), bytes=len(p.payload.encode()))
        else: x.update(max_bytes=p.max_bytes, bytes=min(p.max_bytes, len(resource['content'].encode())))
    elif p.operation == 'agent.spawn':
        require(r.startswith('workspace:'), 'RESOURCE_TYPE', 'Spawn needs an institutional workspace.')
        require(not db.execute('SELECT 1 FROM swarm_children WHERE id=?', (p.child_id,)).fetchone(), 'CHILD_EXISTS', 'A child identity can start only once.')
        if p.program == 'contained-proof':
            from .workers import contract
            c['runtime'] = contract()
        x.update(program=p.program, child_id=p.child_id, scope=p.scope.model_dump(), program_hash=digest(c['runtime']) if p.program == 'contained-proof' else PROGRAM, workspace=r, network=[], environment='fixed-local-simulator')
    else:
        row = db.execute('SELECT data FROM swarm_children WHERE id=?', (p.child_id,)).fetchone()
        require(row is not None, 'UNKNOWN_CHILD', 'Stop requires an existing child.')
        child = decode(row[0])
        require(child['principal_id'] == p.principal_id and child['workspace'] == r, 'CHILD_OWNER', 'The supervisor must represent this child’s principal and workspace.')
        require(grant['parent_grant_id'] is None or child['parent_agent'] == p.agent_id, 'SUPERVISORY_SCOPE', 'Stopping a peer’s child needs independent supervisory authority.')
        require(child['status'] == 'running', 'CHILD_TERMINAL', 'The child has already stopped.')
        x.update(child_id=p.child_id)
        c.update(child_version=child['version'])
    return Effect(p=p.principal_id, s=p.socket_id, o=p.operation, r=r, x=x, c=c)


def delegation_check(child, parent, now):
    require(child['principal_id'] == parent['principal_id'], 'DELEGATION_PRINCIPAL', 'A different principal requires independent institutional authority.')
    require(now < child['exp'] <= parent['exp'], 'DELEGATION_EXPIRY', 'Child authority cannot outlive its parent.')
    require(parent['depth'] < 4, 'DELEGATION_DEPTH', 'This fixture permits four generations.')
    for field in ('operations', 'resources', 'audiences', 'purposes', 'recipients'):
        require(set(child[field]) <= set(parent[field]), 'DELEGATION_EXPANSION', f'Child {field} must be a subset of parent authority.')
    require(set(child['ceilings']) == set(parent['ceilings']), 'DELEGATION_EXPANSION', 'Child must declare every budget unit.')
    require(all(type(v) is int and 0 <= v <= parent['ceilings'][k] for k,v in child['ceilings'].items()), 'DELEGATION_EXPANSION', 'Child per-effect ceilings cannot increase.')


def evaluate(effect, pack, grant, risk, now, approved, breaker):
    e, b = deepcopy(effect), {k: 0 for k in pack['limits']}
    try:
        require(not breaker, 'BREAKER_HALT', 'Campaign authority is halted. Existing effects are not undone.')
        require(grant['active'] and now < grant['exp'], 'GRANT_INACTIVE', 'This grant is inactive.')
        require(e['p'] == grant['principal_id'] and e['s'] in grant['audiences'], 'GRANT_BINDING', 'Principal and audience must be in the independent grant.')
        require(e['s'] == SOCKETS[e['o']], 'WRONG_AUDIENCE', 'The operation belongs to a different protected socket.')
        require(e['o'] in grant['operations'], 'GRANT_SCOPE', 'A peer message cannot expand the permitted operations.')
        require(e['r'] in pack['resources'] and e['r'] in grant['resources'], 'RESOURCE_DENIED', 'Reachability, a simulation label or a bearer credential does not authorize this canonical resource.')
        require(e['x']['purpose'] in pack['purposes'] and e['x']['purpose'] in grant['purposes'], 'PURPOSE_DENIED', 'Purpose is outside institutional scope.')
        x = e['x']
        if e['o'].startswith('artifact.'):
            require(x['recipient'] in pack['recipients'] and x['recipient'] in grant['recipients'], 'RECIPIENT_DENIED', 'The communication recipient is outside task scope.')
            require(x['recipient'] == e['c']['resource']['namespace'], 'NAMESPACE_BINDING', 'A namespace label cannot disguise a different recipient.')
            if e['o'] == 'artifact.write': b['messages'], b['disclosed_bytes'] = 1, x['bytes']
            else: b['read_bytes'] = x['bytes']
        elif e['o'] in ('egress.publish', 'resource.access'):
            require(e['r'] in e['c']['credential_resources'], 'CREDENTIAL_SCOPE', 'Credential reference does not authenticate this resource; bearer possession alone still cannot authorize it.')
            require(x['route'] in pack['routes'] and x['route'] in grant['routes'], 'ROUTE_DENIED', 'The route is outside authority.')
            b['disclosed_bytes' if e['o'] == 'egress.publish' else 'read_bytes'] = x['bytes']
        elif e['o'] == 'agent.spawn':
            delegation_check(x['scope'], grant, now)
            b['agent_starts'], b['agent_slots'] = 1, 1
        for k,v in b.items(): require(v <= min(pack['per_action'][k], grant['ceilings'][k]), 'ACTION_LIMIT', f'{k} exceeds a per-effect ceiling.')
        admission(risk, b)
        if not approved and any(b[k] > v for k,v in pack['approval_above'].items()):
            return {'verdict': 'escalate', 'code': 'HUMAN_APPROVAL', 'message': 'Review the exact resolved effect.', 'effect': e, 'bound': b}
        return {'verdict': 'allow', 'code': 'POLICY_ALLOW', 'message': 'Policy permits this exact effect; issuance must reserve capacity.', 'effect': e, 'bound': b}
    except SAACError as err:
        return {'verdict': 'degrade' if breaker else 'deny', 'code': err.code, 'message': err.message, 'effect': e, 'bound': {k: 0 for k in b}}
