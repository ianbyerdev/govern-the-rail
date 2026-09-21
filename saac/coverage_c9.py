"""C9: revocation stops new work without erasing accepted child occupancy.

The sole negative-control deviation is explicitly marked unsupported release.
It never changes the baseline reconciler's handling of the resulting late fill.
"""
from copy import deepcopy
from pathlib import Path

from .authority import live_grant
from .coverage_common import BUDGET, SCALE, UNITS, checkpoint, finish_branch, order_service
from .domain_models import CancelProposal, OrderProposal
from .models import SAACError, require
from .profiles import delegation_check, evaluate, resolve


def configure_grants(service):
    with service.book.transaction() as db:
        parent = service.book.grant(db, 'G-DEMO')
        child = {**parent, 'id': 'G-CHILD', 'agent_id': 'actor.child',
                 'parent_grant_id': parent['id'], 'parent_version': parent['version'],
                 'depth': parent['depth']+1, 'chain': [parent['id']],
                 'operations': ['order.submit'], 'ceilings': {BUDGET: 6*SCALE},
                 'exp': parent['exp']-1}
        delegation_check(child, parent, service.now())
        competitor = {**parent, 'id': 'G-COMPETITOR', 'agent_id': 'actor.competitor',
                      'operations': ['order.submit'], 'ceilings': {BUDGET: 6*SCALE}}
        cleanup = {**parent, 'id': 'G-CLEANUP', 'agent_id': 'institution.cleanup',
                   'operations': ['order.cancel'], 'ceilings': {BUDGET: 0}}
        for grant in (child, competitor, cleanup):
            service.book.save_grant(db, grant)
            service.book.event(db, None, 'INSTITUTIONAL_GRANT_ISSUED', {'grant': grant}, service.now())
    return {'parent': parent, 'child': child, 'competitor': competitor, 'cleanup': cleanup}


def revoke_parent(service, parent_id='G-DEMO'):
    with service.book.transaction() as db:
        parent = service.book.grant(db, parent_id)
        parent['active'] = False
        service.book.save_grant(db, parent)
        service.book.event(db, None, 'PARENT_GRANT_REVOKED', {'grant': parent,
            'accounting_release_authority': False}, service.now())


def invalid_revoke_release(service, reservation_id):
    """Experiment-only mutation; never routed from the actor or public API."""
    with service.book.transaction() as db:
        row = db.execute('SELECT * FROM reservations WHERE id=?', (reservation_id,)).fetchone()
        before = service.book.risk(db)
        values = service.book.allocation(db, row)
        settlement = {'consumed': {k: v['consumed'] for k, v in values.items()},
                      'released': {k: v['bound']-v['consumed'] for k, v in values.items()}}
        service.book.settle_dimensions(db, row, settlement)
        db.execute("UPDATE reservations SET released=bound-consumed,status='released' WHERE id=?", (row['id'],))
        run = service.book.run(db, row['run_id'])
        run.update(status='released', experiment_invalid_release=True, risk_after=service.book.risk(db))
        service.book.save_run(db, run)
        service.book.event(db, row['run_id'], 'EXPERIMENT_INVALID_REVOKE_RELEASE', {
            'reservation_id': row['id'], 'risk_before': before, 'risk_after': service.book.risk(db),
            'terminal_evidence': None, 'reason': 'Revocation is deliberately mistaken for cancellation.',
            'evidence_class': 'deliberately_invalid_experimental_accounting_transition',
            'normal_reconciler_bypassed_for_this_transition_only': True}, service.now())


def accept_or_record_rejection(service, receipt):
    try:
        return {'accepted': True, 'receipt': receipt, 'result': service.reconciler.accept(receipt)}
    except SAACError as error:
        rejection = {'accepted': False, 'receipt': receipt, 'code': error.code, 'message': error.message}
        with service.book.transaction() as db:
            row = db.execute('SELECT run_id FROM reservations WHERE id=?', (receipt['reservation_id'],)).fetchone()
            service.book.event(db, row['run_id'], 'LATE_EVIDENCE_REJECTED', rejection, service.now())
        return rejection


def run_c9(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    branches = {}
    for name in ('safe', 'unsafe'):
        service = order_service(directory/name)
        grants = configure_grants(service)
        child_proposal = OrderProposal(quantity=6, limit_price_cents=SCALE,
                                      agent_id='actor.child', grant_id='G-CHILD')
        child = service.authority.propose(child_proposal, request_id='c9:child')
        require(child['status'] == 'authorized', 'SCHEDULE', 'Child must receive authority.')
        accepted = service.execute(child['id'])
        require(accepted['attempt']['accepted'], 'SCHEDULE', 'Child must be accepted by the rail.')
        checkpoints = [checkpoint(service, 'child_accepted')]
        service.advance(1)
        revoke_parent(service)
        if name == 'unsafe':
            invalid_revoke_release(service, child['reservation_id'])
        revoked = service.authority.propose(child_proposal, request_id='c9:revoked-new-work')
        require(revoked['decision']['code'] == 'GRANT_INACTIVE', 'SCHEDULE', 'Revoked lineage must fail.')
        checkpoints.append(checkpoint(service, 'parent_revoked'))
        service.advance(1)
        proposal = OrderProposal(quantity=6, limit_price_cents=SCALE,
                                 agent_id='actor.competitor', grant_id='G-COMPETITOR')
        with service.book.transaction() as db:
            grant = live_grant(service.book, db, proposal.grant_id, service.now())
            require(grant['parent_grant_id'] is None and not grant['chain'], 'SCHEDULE',
                    'Competitor must be outside the revoked subtree.')
            state = service.book.state(db)
            neutral_risk = deepcopy(service.book.risk(db))
            neutral_risk['budgets'][BUDGET].update(used=0, reserved=0, available=10*SCALE)
            policy_check = evaluate(resolve(proposal, state, grant, db).model_dump(), state['pack'], grant,
                                    neutral_risk, service.now(), False, state['breaker'])
            require(policy_check['verdict'] == 'allow', 'SCHEDULE', 'Competitor must pass non-capacity checks.')
            service.book.event(db, None, 'COMPETITOR_SCOPE_CHECKED', {
                'grant': grant, 'policy_with_zero_occupancy': policy_check,
                'explanation': 'Read-only counterfactual checks policy and scope; actual admission uses real occupancy.'}, service.now())
        competitor = service.authority.propose(proposal, request_id='c9:competitor')
        if name == 'safe':
            require(competitor['status'] == 'denied' and competitor['decision']['code'] == 'SESSION_LIMIT',
                    'SCHEDULE', 'Safe competitor denial must be insufficient shared headroom.')
        else:
            require(competitor['status'] == 'authorized', 'SCHEDULE', 'Unsafe control must admit competitor.')
            require(service.execute(competitor['id'])['attempt']['accepted'], 'SCHEDULE',
                    'Unsafe competitor must create actual accepted occupancy.')
        checkpoints.append(checkpoint(service, 'competitor'))
        service.advance(1)
        order_id = accepted['execution']['result']['order']['id']
        fill = service.socket.fill_order(order_id, 2, SCALE)
        fill_delivery = accept_or_record_rejection(service, fill)
        cleanup_proposal = CancelProposal(order_id=order_id, agent_id='institution.cleanup', grant_id='G-CLEANUP')
        cleanup = service.authority.propose(cleanup_proposal, request_id='c9:institutional-cleanup')
        require(cleanup['status'] == 'authorized', 'SCHEDULE', 'Independent cleanup must remain authorized.')
        cleanup_result = service.execute(cleanup['id'])
        require(cleanup_result['attempt']['accepted'], 'SCHEDULE', 'Cleanup must cancel the residual four units.')
        cancellation = service.socket.evidence(child['reservation_id'])
        cancel_delivery = accept_or_record_rejection(service, cancellation)
        if name == 'unsafe':
            require(not fill_delivery['accepted'] and not cancel_delivery['accepted'], 'SCHEDULE',
                    'Normal reconciler must retain late-evidence rejection after invalid release.')
        else:
            require(fill_delivery['accepted'] and cancel_delivery['accepted'], 'SCHEDULE',
                    'Safe historical work must settle despite parent revocation.')
        checkpoints.append(checkpoint(service, 'child_completed'))
        branches[name] = finish_branch(service, checkpoints, grants=grants,
            revoked_new_request=revoked, competitor=competitor, competitor_scope_check=policy_check,
            cleanup=cleanup_result, late_evidence=[fill_delivery, cancel_delivery],
            intended_breach_detected=name == 'unsafe' and any(not c['coverage'] for c in checkpoints))
    return {'schema_version': 1, 'case': 'C9', 'title': 'Revoking a parent does not erase a child’s commitment',
            'units': UNITS, 'branches': branches, 'status': 'implemented/unpinned',
            'topology': 'Each matched branch uses one baseline Runtime/Gateway SQLite fixture; no remote-process claim.',
            'schedule': ['child_accepted', 'parent_revoked', 'competitor', 'child_completed'],
            'limitations': ['Synthetic orders and trusted same-host controls.',
                           'Evaluator observations are not institutional evidence; only ordinary signed receipts reconcile.']}
