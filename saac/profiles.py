"""Trusted, deterministic domain profiles, never actor-supplied adapters."""
from copy import deepcopy
from pathlib import Path
import hashlib
from .crypto import digest
from .models import Effect, require, SAACError
from .domain_models import DomainPolicy, OrderProposal, ReferralProposal, JobProposal
from .accounting import admission

PROFILE_OPERATIONS = {'trading': ['order.submit', 'order.cancel'], 'referrals': ['referral.release'],
                      'runtime': ['job.run', 'job.delegate']}
AUDIENCES = {'payments': 'PAYMENTS-1', 'trading': 'OMS-17', 'referrals': 'REFERRAL-EGRESS-1', 'runtime': 'RUNTIME-1'}
from .swarm.models import OPERATIONS as SWARM_OPERATIONS
PROFILE_OPERATIONS['swarm'] = SWARM_OPERATIONS
AUDIENCES['swarm'] = 'ARTIFACT-1'
SCRIPT = Path(__file__).parent / 'fixtures/audit.py'


def profile_for(operation):
    for profile, operations in PROFILE_OPERATIONS.items():
        if operation in operations: return profile
    return 'payments'


def schema_hash(profile):
    from .domain_models import PROPOSALS
    return digest({'profile': profile, 'version': 1, 'schemas': {o: PROPOSALS[o].model_json_schema() for o in PROFILE_OPERATIONS[profile]}})


def seed(profile):
    if profile == 'swarm':
        from .swarm.profile import seed as swarm_seed
        return swarm_seed()
    require(profile in PROFILE_OPERATIONS, 'PROFILE', 'Unknown rail profile.')
    common = {'profile': profile, 'route_versions': {}, 'breaker': False, 'clock_offset': 0}
    if profile == 'trading':
        policy = DomainPolicy(limits={'notional_usd_cents': 10000000}, per_action={'notional_usd_cents': 9000000},
                              approval_above={'notional_usd_cents': 8500000}, resources=['instrument:XYZ/book:CASH-1'], routes=['BROKER-A/session17'])
        common.update(resources={'instrument:XYZ': {'version': 1, 'symbol': 'XYZ'}}, aliases={'xyz': 'instrument:XYZ', 'xyz.us': 'instrument:XYZ', 'instrument:xyz': 'instrument:XYZ'},
                      books={'CASH-1': {'version': 1}}, route_versions={'BROKER-A/session17': 1, 'BROKER-B/session19': 1}, reference_version=1)
    elif profile == 'referrals':
        documents = {f'doc-{i}': {'patient': 'patient:P-104', 'version': 1,
            'content': f'SYNTHETIC RECORD {i}: fictional referral data, no clinical meaning.', 'classification': 'synthetic-protected'} for i in range(1, 8)}
        policy = DomainPolicy(limits={'records': 10, 'bytes': 10000}, per_action={'records': 6, 'bytes': 5000},
                              approval_above={'records': 4}, resources=['patient:P-104'], purposes=['referral'], recipients=['recipient:north'])
        common.update(resources={'patient:P-104': {'version': 1}, 'patient:P-205': {'version': 1}},
                      aliases={'p-104': 'patient:P-104', 'patient:p-104': 'patient:P-104', 'demo-patient': 'patient:P-104', 'p-205': 'patient:P-205'},
                      documents=documents, consent={'patient:P-104': {'version': 7, 'active': True, 'purposes': ['referral'], 'recipients': ['recipient:north']}},
                      recipients={'recipient:north': {'version': 2, 'name': 'North Clinic (synthetic)', 'endpoint': 'local-inbox:north'}, 'recipient:south': {'version': 1, 'name': 'South Clinic (synthetic)', 'endpoint': 'local-inbox:south'}},
                      recipient_aliases={'north-clinic': 'recipient:north', 'north': 'recipient:north', 'recipient:north': 'recipient:north', 'south-clinic': 'recipient:south'})
    else:
        from .runner import runtime_contract
        policy = DomainPolicy(limits={'job_starts': 3, 'job_slots': 1}, per_action={'job_starts': 1, 'job_slots': 1}, resources=['workspace:demo'])
        common.update(resources={'workspace:demo': {'version': 1}}, aliases={'demo': 'workspace:demo', 'workspace:demo': 'workspace:demo'},
                      runtime=runtime_contract())
    grant = {'resources': policy.resources, 'routes': policy.routes, 'purposes': policy.purposes, 'recipients': policy.recipients,
             'operations': PROFILE_OPERATIONS[profile], 'ceilings': policy.per_action, 'network': []}
    return common, policy, grant


def default_proposal(profile):
    return {'trading': OrderProposal, 'referrals': ReferralProposal, 'runtime': JobProposal}[profile]()


def resolve(proposal, state, grant, db=None):
    if proposal.operation in SWARM_OPERATIONS:
        from .swarm.profile import resolve as swarm_resolve
        return swarm_resolve(proposal, state, grant, db)
    from .resolver import alias
    from .risk_book import decode
    profile = profile_for(proposal.operation)
    require(state.get('profile') == profile, 'PROFILE_BINDING', 'This operation belongs to another institutional book.')
    c = {'pack_hash': state['pack_hash'], 'grant_version': grant['version'], 'schema_hash': schema_hash(profile), 'profile': profile}
    if proposal.operation == 'order.submit':
        r = state['aliases'].get(alias(proposal.instrument))
        require(r in state['resources'] and proposal.book in state['books'], 'UNKNOWN_RESOURCE', 'Unknown canonical instrument or book.')
        require(proposal.route in state['route_versions'], 'UNKNOWN_ROUTE', 'Unknown execution route.')
        c.update(resource_version=state['resources'][r]['version'], book_version=state['books'][proposal.book]['version'],
                 route_version=state['route_versions'][proposal.route], reference_version=state['reference_version'])
        r += '/book:'+proposal.book
        x = {k: getattr(proposal, k) for k in ('side', 'quantity', 'limit_price_cents', 'route', 'time_in_force')}
        x['currency'] = 'USD'
    elif proposal.operation == 'order.cancel':
        row = db.execute('SELECT data FROM orders WHERE id=?', (proposal.order_id,)).fetchone()
        require(row is not None, 'UNKNOWN_ORDER', 'Only a canonical existing order can be cancelled.')
        order = decode(row[0])
        require(order['principal_id'] == proposal.principal_id, 'ORDER_OWNER', 'Cancel authority is limited to the represented principal’s orders.')
        r, x = 'order:'+proposal.order_id, {'order_id': proposal.order_id, 'original_resource': order['resource']}
        c.update(order_version=order['version'], order_status=order['status'])
    elif proposal.operation == 'referral.release':
        r = state['aliases'].get(alias(proposal.patient))
        recipient = state['recipient_aliases'].get(alias(proposal.recipient))
        require(r in state['resources'] and recipient in state['recipients'], 'UNKNOWN_RESOURCE', 'Resolve the patient and recipient independently.')
        require(len(set(proposal.documents)) == len(proposal.documents), 'MANIFEST_DUPLICATE', 'Each record occurs once in a sealed manifest.')
        manifest = []
        for key in sorted(proposal.documents):
            document = state['documents'].get(key)
            require(document is not None and document['patient'] == r, 'PATIENT_BINDING', 'Every document must belong to the resolved patient.')
            content = document['content'].encode()
            manifest.append({'id': key, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                             'version': document['version'], 'classification': document['classification']})
        consent = state['consent'].get(r, {'version': 0, 'active': False, 'purposes': [], 'recipients': []})
        x = {'manifest': manifest, 'manifest_hash': digest(manifest), 'recipient': recipient, 'endpoint': state['recipients'][recipient]['endpoint'], 'purpose': proposal.purpose}
        c.update(patient_version=state['resources'][r]['version'], consent=consent, recipient_version=state['recipients'][recipient]['version'])
    elif proposal.operation == 'job.delegate':
        r, x = 'grant:'+grant['id'], proposal.delegation.model_dump()
    else:
        from .runner import runtime_contract
        require(proposal.script == 'audit', 'SCRIPT_DENIED', 'Only the fixed, inspectable audit fixture may execute.')
        r = state['aliases'].get(alias(proposal.workspace))
        require(r in state['resources'], 'UNKNOWN_RESOURCE', 'Unknown constrained workspace.')
        # Bind the currently installed code and runner contract as well as stored policy.
        c.update(workspace_version=state['resources'][r]['version'], runtime=runtime_contract(), policy_runtime=state['runtime'])
        x = {'script': 'audit', 'argv': proposal.argv, 'network': proposal.network, 'workspace': r,
             'environment': {'LANG': 'C.UTF-8'}, 'mounts': ['runtime:read-only', 'workspace:read-write', 'institution-marker:read-only'],
             'timeout_seconds': 5, 'child_grant_id': grant['id']}
    return Effect(p=proposal.principal_id, s=proposal.socket_id, o=proposal.operation, r=r, x=x, c=c)


def delegation_check(child, parent, now):
    require(child['principal_id'] == parent['principal_id'], 'DELEGATION_PRINCIPAL', 'Cross-principal authority needs an independent grant.')
    require(now < child['exp'] <= parent['exp'], 'DELEGATION_EXPIRY', 'A child cannot outlive its parent.')
    for field in ('resources', 'operations', 'network'):
        require(set(child[field]) <= set(parent[field]), 'DELEGATION_EXPANSION', f'Child {field} expands authority.')
    require(set(child['ceilings']) == set(parent['ceilings']), 'DELEGATION_EXPANSION', 'All budget ceilings must be explicit.')
    for key, value in child['ceilings'].items():
        require(type(value) is int and 0 <= value <= parent['ceilings'][key], 'DELEGATION_EXPANSION', 'Child budget ceilings cannot increase.')
    require(parent['depth'] < 4, 'DELEGATION_DEPTH', 'At most four delegation generations are supported.')


def evaluate(effect, pack, grant, risk, now, approved, breaker):
    if effect['o'] in SWARM_OPERATIONS:
        from .swarm.profile import evaluate as swarm_evaluate
        return swarm_evaluate(effect, pack, grant, risk, now, approved, breaker)
    e = deepcopy(effect)
    bound = {k: 0 for k in pack['limits']}
    try:
        require(not breaker, 'BREAKER_HALT', 'The institution halted new effects.')
        require(grant['active'] and now < grant['exp'], 'GRANT_INACTIVE', 'The grant is no longer active.')
        require(e['p'] == grant['principal_id'] and e['s'] == grant['socket_id'], 'GRANT_BINDING', 'Principal and audience must match the independent grant.')
        require(e['o'] in grant['operations'], 'GRANT_SCOPE', 'This operation is outside the grant.')
        resource = e['x']['original_resource'] if e['o'] == 'order.cancel' else e['r']
        if e['o'] == 'job.delegate':
            delegation_check(e['x'], grant, now)
        else:
            require(resource in grant['resources'] and resource in pack['resources'], 'RESOURCE_DENIED', 'Canonical resource is outside the pack or grant.')
        if e['o'] == 'order.submit':
            x = e['x']
            require(x['route'] in pack['routes'] and x['route'] in grant['routes'], 'ROUTE_DENIED', 'This execution route is not permitted.')
            require(pack['price_min_cents'] <= x['limit_price_cents'] <= pack['price_max_cents'], 'PRICE_COLLAR', 'The limit price is outside the independent price collar.')
            bound['notional_usd_cents'] = x['quantity']*x['limit_price_cents']
        elif e['o'] == 'order.cancel':
            require(e['c']['order_status'] in ('working', 'partial'), 'ORDER_TERMINAL', 'A terminal order cannot be cancelled again.')
        elif e['o'] == 'referral.release':
            x, consent = e['x'], e['c']['consent']
            require(consent['active'] and x['purpose'] in consent['purposes'] and x['recipient'] in consent['recipients'], 'CONSENT_DENIED', 'Current consent does not cover this exact purpose and destination.')
            require(x['purpose'] in pack['purposes'] and x['purpose'] in grant['purposes'], 'PURPOSE_DENIED', 'Purpose is outside institutional scope.')
            require(x['recipient'] in pack['recipients'] and x['recipient'] in grant['recipients'], 'RECIPIENT_DENIED', 'Recipient is outside institutional scope.')
            bound = {'records': len(x['manifest']), 'bytes': sum(d['bytes'] for d in x['manifest'])}
        elif e['o'] == 'job.run':
            require(not e['x']['network'] and not e['x']['argv'], 'JOB_SCOPE', 'The fixed job has no network or extra arguments.')
            require(e['c']['runtime'] == e['c']['policy_runtime'], 'RUNTIME_STALE', 'Runtime code changed; institution must publish a fresh job contract.')
            bound = {'job_starts': 1, 'job_slots': 1}
        for key, amount in bound.items():
            require(amount <= min(pack['per_action'][key], grant['ceilings'][key]), 'ACTION_LIMIT', f'{key} exceeds the per-action ceiling.')
        admission(risk, bound)
        if not approved and any(bound[k] > v for k, v in pack['approval_above'].items()):
            return {'verdict': 'escalate', 'code': 'HUMAN_APPROVAL', 'message': 'Review this sealed exact effect before authority can be issued.', 'effect': e, 'bound': bound}
        return {'verdict': 'allow', 'code': 'POLICY_ALLOW', 'message': 'The effect cleared the live policy. A verdict is not authority.', 'effect': e, 'bound': bound}
    except SAACError as error:
        return {'verdict': 'degrade' if breaker else 'deny', 'code': error.code, 'message': error.message, 'effect': e, 'bound': {k: 0 for k in bound}}
