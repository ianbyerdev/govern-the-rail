"""Small operator experiments over the existing core, not another authority.

Each case gets one isolated book. All participants in that case share its limits.
The unsafe baseline lives in workbench.race and never reaches a protected rail.
"""
from .adapters import propose_via
from .models import PolicyConfig, Scope, require
from .sdk import SAACClient, Identity
from .service import ServiceTransport

SCENARIOS = {'normal', 'rejection', 'bypass', 'replay', 'mutation', 'expiry', 'delegation', 'retry'}


def demonstrate(store, scenario, integration='native'):
    require(scenario in SCENARIOS, 'SCENARIO', 'Unknown architecture demonstration.')
    book_id, svc = store.create('payments')
    svc.publish(PolicyConfig(version=2, session_limit_cents=100_000_000,
                             per_action_cents=1_000_000, approval_above_cents=1_000_000))
    transport = ServiceTransport(svc)
    sdk = SAACClient(transport, Identity())
    attempts, lineage = [], []

    def propose(client, intent):
        return propose_via(integration, client, intent)

    def execute(client, run, label, proposal=None):
        result = client.execute(proposal or run.get('execution_proposal', run['proposal']), run['kappa'])
        attempts.append({'label': label, 'run_id': run['id'], **result})
        if result['accepted']:
            client.reconcile(result['receipt'])
        return result

    def outcome(label, run):
        attempts.append({'label': label, 'run_id': run['id'], 'accepted': 'kappa' in run,
                         'code': run['decision']['code'], 'stage': 'authority'})

    intent = {'amount_cents': 100_000, 'beneficiary': 'acme'}
    if scenario == 'retry':
        # Simulate a lost authorization response, before any execution/settlement.
        run = propose_via(integration, sdk, intent, request_id='lost-response-demo')
        reserved_before = svc.view()['risk']['reserved_cents']
        retried = propose_via(integration, sdk, intent, request_id='lost-response-demo')
        reserved_after = svc.view()['risk']['reserved_cents']
        require(run['kappa'] == retried['kappa'] and reserved_before == reserved_after,
                'RETRY_INVARIANT', 'Retry must return the original authority without another reservation.')
        attempts.append({'label': 'Lost response retried: same κ and one $1,000 reservation',
                         'run_id': run['id'], 'accepted': True, 'stage': 'authority', 'code': 'AUTHORIZATION_REPLAYED',
                         'status_label': 'REUSED', 'reserved_before': reserved_before, 'reserved_after': reserved_after})
        execute(sdk, retried, 'Original κ executes once')
        execute(sdk, run, 'Retry does not permit a second execution')
    elif scenario == 'delegation':
        # Independent institution establishes the human's $10,000 root ceiling.
        with svc.book.transaction() as db:
            root = svc.book.grant(db, 'G-DEMO')
            root.update(max_amount_cents=1_000_000, version=root['version']+1)
            svc.book.save_grant(db, root)
            svc.book.event(db, None, 'INSTITUTIONAL_GRANT_NARROWED', root, svc.now())
        lineage.append({'label': 'Human authority', 'grant': root})

        def delegate(client, label, amount, operations, resources):
            with svc.book.connect() as db:
                inherited = svc.book.grant(db, client.identity.grant_id)
            scope = Scope(agent_id='agent.'+label.lower().replace(' ', '-'), exp=min(svc.now()+300, inherited['exp']),
                          max_amount_cents=amount, operations=operations, resources=resources)
            issued = propose(client, {'operation': 'dispatch', 'delegation': scope.model_dump()})
            result = execute(client, issued, label+' grant narrowed')
            grant = result['receipt']['result']['child_grant']
            lineage.append({'label': label, 'grant': grant})
            return SAACClient(transport, Identity(grant['principal_id'], grant['agent_id'], grant['id']))

        both = ['beneficiary:acme', 'beneficiary:community']
        parent = delegate(sdk, 'Parent', 1_000_000, ['payment', 'dispatch'], both)
        child_a = delegate(parent, 'Child A', 200_000, ['payment', 'dispatch'], both)
        child_b = delegate(parent, 'Child B', 300_000, ['payment'], both)
        child_c = delegate(parent, 'Child C', 1, [], both)
        child_d = delegate(parent, 'Child D', 100_000, ['payment'], ['beneficiary:acme'])
        execute(child_a, propose(child_a, intent), 'Child A pays $1,000 within $2,000 ceiling')
        execute(child_b, propose(child_b, {'amount_cents': 300_000}), 'Child B pays $3,000 within $3,000 ceiling')
        run = propose(child_a, {'amount_cents': 2_000_000})
        outcome('Child A requests $20,000', run)
        outcome('Child C has no mutation authority', propose(child_c, intent))
        outcome('Child D attempts a beneficiary outside its scope', propose(child_d, {**intent, 'beneficiary': 'community'}))
        scope = Scope(agent_id='agent.grandchild', exp=svc.now()+60, max_amount_cents=2_000_000)
        outcome('Child A tries to manufacture a $20,000 grandchild grant',
                propose(child_a, {'operation': 'dispatch', 'delegation': scope.model_dump()}))
    else:
        if scenario == 'rejection':
            intent['amount_cents'] = 2_000_000
        if scenario == 'expiry':
            intent['ttl_seconds'] = 1
        if scenario == 'bypass':
            # No SDK or authority call on this path. Same rail, same typed action.
            result = svc.redeem(intent, None)
            attempts.append({'label': 'Direct rail call without κ', **result})
        run = propose(sdk, intent)
        if 'kappa' not in run:
            outcome('Policy rejection — no κ, no execution', run)
        else:
            if scenario == 'expiry':
                svc.advance(2)
            proposal = {**run['proposal'], 'beneficiary': 'community'} if scenario == 'mutation' else None
            execute(sdk, run, 'Substitute Account B after authority for Account A' if proposal else
                    'Same action with valid κ' if scenario == 'bypass' else 'Present κ to protected rail', proposal)
            if scenario == 'replay':
                execute(sdk, run, 'Reuse consumed κ')
    from .workbench import bundle
    return bundle(book_id, svc, run, demonstration={'scenario': scenario, 'integration': integration,
        'attempts': attempts, 'lineage': lineage,
        'note': 'One shared book. Child ceilings are per-effect limits; they do not mint independent capacity.' if lineage else
                'The agent proposes. SAAC authorizes. The rail enforces.'})
