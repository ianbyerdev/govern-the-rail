"""Local effects executed only after the common socket independently verifies κ."""
from .risk_book import encode, decode, uid
from .models import require
from .profiles import delegation_check


class OrderRail:
    def execute(self, db, effect, execution_id, mode):
        x = effect['x']
        order = {'id': uid('order'), 'execution_id': execution_id, 'principal_id': effect['p'], 'resource': effect['r'],
                 'version': 1, 'status': 'working', 'quantity': x['quantity'], 'filled': 0, 'notional': 0,
                 'limit_price_cents': x['limit_price_cents'], 'route': x['route']}
        db.execute('INSERT INTO orders VALUES (?,?)', (order['id'], encode(order)))
        return {'status': 'working', 'order': order, 'consumed': {'notional_usd_cents': 0}, 'released': {'notional_usd_cents': 0}}


class CancelOrderRail:
    def execute(self, db, effect, execution_id, mode):
        row = db.execute('SELECT data FROM orders WHERE id=?', (effect['x']['order_id'],)).fetchone()
        order = decode(row[0])
        require(order['status'] in ('working', 'partial'), 'ORDER_TERMINAL', 'The order is already closed.')
        order.update(status='cancelled', version=order['version']+1)
        db.execute('UPDATE orders SET data=? WHERE id=?', (encode(order), order['id']))
        return {'status': 'cancelled', 'order': order, 'consumed': {'notional_usd_cents': 0}, 'released': {'notional_usd_cents': 0},
                'related_execution_id': order['execution_id']}


class ReferralRail:
    def __init__(self, book): self.book = book

    def execute(self, db, effect, execution_id, mode):
        # The resolver just verified current hashes under this same write lock.
        state, x = self.book.state(db), effect['x']
        packet = {'patient': effect['r'], 'recipient': x['recipient'], 'endpoint': x['endpoint'], 'manifest_hash': x['manifest_hash'],
                  'manifest': x['manifest'], 'records': [{'id': entry['id'], 'content': state['documents'][entry['id']]['content']} for entry in x['manifest']]}
        db.execute('INSERT INTO inbox VALUES (?,?)', (execution_id, encode(packet)))
        consumed = {'records': len(x['manifest']), 'bytes': sum(d['bytes'] for d in x['manifest'])}
        return {'status': 'disclosed', 'manifest_hash': x['manifest_hash'], 'patient': effect['r'], 'recipient': x['recipient'],
                'consumed': consumed, 'released': {'records': 0, 'bytes': 0}}


class JobRail:
    def execute(self, db, effect, execution_id, mode):
        job = {'id': execution_id, 'status': 'queued', 'effect': effect, 'launch_count': 0}
        db.execute('INSERT INTO jobs VALUES (?,?)', (execution_id, encode(job)))
        return {'status': 'queued', 'job_id': execution_id, 'consumed': {'job_starts': 0, 'job_slots': 0}, 'released': {'job_starts': 0, 'job_slots': 0}}


class JobDelegationRail:
    def __init__(self, book, clock): self.book, self.clock = book, clock

    def execute(self, db, effect, execution_id, mode):
        parent = self.book.grant(db, effect['r'].removeprefix('grant:'))
        delegation_check(effect['x'], parent, self.clock())
        child = {**parent, **effect['x'], 'id': uid('grant'), 'parent_grant_id': parent['id'], 'parent_version': parent['version'],
                 'depth': parent['depth']+1, 'version': 1, 'active': True, 'chain': [*parent['chain'], parent['id']]}
        self.book.save_grant(db, child)
        return {'status': 'delegated', 'child_grant': child, 'consumed': {'job_starts': 0, 'job_slots': 0}, 'released': {'job_starts': 0, 'job_slots': 0}}
