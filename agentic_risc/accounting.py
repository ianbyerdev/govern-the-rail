"""Integer budget vectors shared by all profiles; no actor counters are inputs."""
from .models import require


def vector(value):
    return {'amount_cents': value} if type(value) is int else value


def dimensions(risk):
    if 'budgets' in risk:
        return risk['budgets']
    return {'amount_cents': {'used': risk['used_cents'], 'reserved': risk['reserved_cents'],
                            'limit': risk['limit_cents'], 'available': risk['available_cents']}}


def admission(risk, bound):
    budgets, bound = dimensions(risk), vector(bound)
    require(set(bound) == set(budgets), 'BUDGET_SCHEMA', 'Every profile budget dimension must be explicit.')
    for key, amount in bound.items():
        require(type(amount) is int and amount >= 0, 'BUDGET_AMOUNT', 'Bounds are nonnegative integers in named units.')
        require(budgets[key]['used']+budgets[key]['reserved']+amount <= budgets[key]['limit'],
                'SESSION_LIMIT', f'Authoritative {key} capacity is insufficient, including outstanding reservations.')


def settlement(bound, consumed, released):
    b, u, r = vector(bound), vector(consumed), vector(released)
    require(set(b) == set(u) == set(r), 'RECEIPT_SCHEMA', 'Receipt dimensions must exactly match the reservation.')
    q = {}
    for key in b:
        require(all(type(v) is int and v >= 0 for v in (b[key], u[key], r[key])) and u[key]+r[key] <= b[key],
                'RECEIPT_CONSERVATION', 'Evidence cannot exceed or change the reserved allocation.')
        q[key] = b[key]-u[key]-r[key]
    status = 'partial' if any(q.values()) else 'released' if not any(u.values()) and any(b.values()) else 'settled'
    return {'consumed': u, 'released': r, 'remaining': q, 'reservation_status': status}
