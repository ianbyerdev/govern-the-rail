"""Read-only evidence helpers for the v3.9 synthetic coverage schedules.

These helpers observe the fixture. They are never an accepted-evidence interface
and do not change Runtime allocation from what an evaluator can see.
"""
from copy import deepcopy
from pathlib import Path

from .domain_models import DomainPolicy
from .crypto import digest
from .risk_book import decode
from .service import SAACService
from .tape import verify_tape
from .models import SAACError

BUDGET = 'notional_usd_cents'
SCALE = 100
EPOCH = 1_790_000_000
UNITS = {'domain': BUDGET, 'scale': SCALE, 'display': 'synthetic units',
         'accounting_window': 'one isolated schedule; no daily reset or cross-domain netting'}


def order_service(directory, limit=1000):
    service = SAACService(Path(directory), clock=lambda: EPOCH, profile='trading')
    service.publish(DomainPolicy(version=2, limits={BUDGET: limit},
        per_action={BUDGET: 1000}, resources=['instrument:XYZ/book:CASH-1'],
        routes=['BROKER-A/session17'], price_min_cents=SCALE, price_max_cents=SCALE))
    return service


def export_book(service):
    """JSON-safe snapshot, deliberately excluding databases and private keys."""
    with service.book.transaction() as db:
        result = {'events': service.book.events(db), 'state': service.book.state(db),
                  'risk': service.book.risk(db),
                  'keys': {'riskbook-1': service.issuer.public, 'socket-1': service.socket_signer.public}}
        for table in ('runs', 'orders', 'receipts', 'executions', 'packs', 'grants'):
            result[table] = [decode(r[0]) for r in db.execute(f'SELECT data FROM {table} ORDER BY rowid')]
        result['reservations'] = [{**dict(r), 'kappa': decode(r['kappa'])}
                                  for r in db.execute('SELECT * FROM reservations ORDER BY rowid')]
        result['allocations'] = {r['reservation_id']: decode(r['data']) for r in db.execute('SELECT * FROM allocations')}
        result['nonces'] = [dict(r) for r in db.execute('SELECT * FROM nonces')]
    result['head'] = result['events'][-1]['hash'] if result['events'] else 'genesis'
    result['attestation'] = service.issuer.sign({'typ': 'coverage-book-checkpoint',
        'head': result['head'], 'snapshot_digest': digest(result), 'now': service.now(),
        'evidence_class': 'local_fixture_attestation_not_accepted_outcome_authority'})
    return result


def observe_book(book, now):
    """Join issued lifecycles with signed execution outcomes, never release labels.

    Cancellation commands themselves have zero bound; their order image is not
    counted again. Unredeemed, unexpired promises count before rail acceptance.
    """
    receipts = {}
    for receipt in book['receipts']:
        key = receipt['kappa_id']
        if key not in receipts or receipt['revision'] > receipts[key]['revision']:
            receipts[key] = receipt
    issued = [e['data'] for e in book['events'] if e['kind'] == 'CAPABILITY_ISSUED']
    lifecycles = []
    for capability in issued:
        bound = capability['reservation']['bound'][BUDGET]
        if bound == 0:
            continue
        receipt = receipts.get(capability['kappa_id'])
        if receipt:
            result = receipt['result']
            if result['status'] == 'non_use':
                consumed, remaining, domain = 0, 0, 'accepted_non_use'
            else:
                order = result['order']
                consumed = order['notional']
                remaining = ((order['quantity'] - order['filled']) * order['limit_price_cents']
                             if order['status'] in ('working', 'partial') else 0)
                domain = 'accepted_rail_execution'
        else:
            consumed = 0
            remaining = bound if capability['iat'] <= now < capability['exp'] else 0
            domain = 'usable_unredeemed_promise' if remaining else 'expired_unredeemed_promise'
        lifecycles.append({'kappa_id': capability['kappa_id'], 'reservation_id': capability['reservation']['id'],
                           'execution_id': receipt['execution_id'] if receipt else None,
                           'consumed': consumed, 'residual': remaining, 'E': consumed + remaining,
                           'evidence_domain': domain})
    promises = [v for v in lifecycles if v['evidence_domain'] == 'usable_unredeemed_promise']
    return {'E': sum(v['E'] for v in lifecycles), 'lifecycles': lifecycles,
            'outstanding_promises': len(promises), 'promise_obligation': sum(v['E'] for v in promises),
            'evidence_domain': 'issuance_joined_with_rail' if promises else 'rail_execution_journal',
            'oracle_is_accepted_evidence': False}


def checkpoint(service, checkpoint_id, **extra):
    snapshot = export_book(service)
    oracle = observe_book(snapshot, service.now())
    risk = snapshot['risk']['budgets'][BUDGET]
    used, reserved, limit = risk['used'], risk['reserved'], risk['limit']
    return {'id': checkpoint_id, 'now': service.now(), 'U': used, 'Q': reserved, 'L': limit,
            'E': oracle['E'], 'available': limit-used-reserved,
            'ledger_compliant': used+reserved <= limit, 'coverage': oracle['E'] <= used+reserved,
            'shared_ceiling_compliant': oracle['E'] <= limit,
            'coverage_gap': oracle['E']-used-reserved,
            'outstanding_promises': oracle['outstanding_promises'],
            'evidence_domain': oracle['evidence_domain'], 'oracle': oracle, 'snapshot': snapshot, **extra}


def ordinary_tape(book):
    try:
        return verify_tape(book['events'], book['keys'], book['head'])
    except SAACError as error:
        return {'valid': False, 'code': error.code, 'message': error.message}


def finish_branch(service, checkpoints, **extra):
    book = export_book(service)
    ordinary = ordinary_tape(book)
    conformance = ordinary['valid'] and all(c['ledger_compliant'] and c['coverage'] and
                                           c['shared_ceiling_compliant'] for c in checkpoints)
    return {'checkpoints': checkpoints, 'book': book, 'admissions': deepcopy(book['runs']),
            'ordinary_tape': ordinary, 'ordinary_conformance': conformance,
            'historical_violation': not all(c['coverage'] and c['shared_ceiling_compliant'] for c in checkpoints),
            **extra}
