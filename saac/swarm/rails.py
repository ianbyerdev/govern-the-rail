"""Local effects execute under the socket's redemption transaction."""
from ..risk_book import encode, decode
from ..models import require
from .models import BUDGETS

class SwarmRail:
    def __init__(self, book, clock): self.book, self.clock = book, clock

    def execute(self, db, e, execution_id, mode):
        used, released = {k:0 for k in BUDGETS}, {k:0 for k in BUDGETS}
        x, op = e['x'], e['o']
        result = {'status': 'completed', 'consumed': used, 'released': released}
        require(mode == 'full', 'MODE', 'Incident rails use explicit loss/recovery, not partial simulated writes.')
        if op == 'artifact.write':
            # Append-only messages: object version binds the namespace contract, not unrelated writes.
            item = {'execution_id': execution_id, 'resource': e['r'], 'recipient': x['recipient'], 'payload': x['payload'], 'principal': e['p']}
            db.execute('INSERT INTO swarm_messages VALUES (?,?)', (execution_id, encode(item)))
            used.update(messages=1, disclosed_bytes=x['bytes'])
            result['message'] = item
        elif op in ('artifact.read', 'resource.access'):
            content = x['data'].encode() if op == 'artifact.read' else self.book.state(db)['resources'][e['r']]['content'].encode()[:x['bytes']]
            used['read_bytes'] = len(content)
            result['data'] = content.decode(errors='replace')
        elif op == 'egress.publish':
            used['disclosed_bytes'] = x['bytes']
            item = {'execution_id': execution_id, 'resource': e['r'], 'payload': x['payload'], 'bytes': x['bytes'], 'origin': x['origin']}
            db.execute('INSERT INTO swarm_disclosures VALUES (?,?)', (execution_id, encode(item)))
            result['disclosure'] = item
        elif op == 'agent.spawn':
            parent = self.book.grant(db, e['c']['grant_id'])
            scope = x['scope']
            gid = 'G-'+x['child_id']
            child = {**scope, 'id': gid, 'agent_id': x['child_id'], 'socket_id': 'ARTIFACT-1', 'session_id': parent['session_id'],
                     'parent_grant_id': parent['id'], 'parent_version': parent['version'], 'depth': parent['depth']+1,
                     'version': 1, 'active': x['program'] == 'logical', 'chain': parent['chain']+[parent['id']], 'routes': parent['routes'], 'network': []}
            self.book.save_grant(db, child)
            record = {'id': x['child_id'], 'grant_id': gid, 'principal_id': e['p'], 'parent_agent': parent['agent_id'],
                      'workspace': e['r'], 'execution_id': execution_id, 'status': 'running' if x['program']=='logical' else 'queued', 'version': 1, 'program': x['program'], 'program_hash': x['program_hash']}
            db.execute('INSERT INTO swarm_children VALUES (?,?)', (record['id'], encode(record)))
            db.execute('INSERT INTO swarm_actors VALUES (?,?,?)', (record['id'], gid, 'spawned'))
            used['agent_starts'] = 1 if x['program']=='logical' else 0
            result.update(status=record['status'], child=record)
        else:
            child = decode(db.execute('SELECT data FROM swarm_children WHERE id=?', (x['child_id'],)).fetchone()[0])
            child.update(status='stopped', version=child['version']+1)
            db.execute('UPDATE swarm_children SET data=? WHERE id=?', (encode(child), child['id']))
            grant = self.book.grant(db, child['grant_id'])
            grant.update(active=False, version=grant['version']+1)
            self.book.save_grant(db, grant)
            result['child'] = child
        return result
