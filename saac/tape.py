"""Deterministic offline policy, signature, and vector-ledger replay."""
from .crypto import digest
from .protocol import verify_artifact as verify
from .packs import evaluate
from .models import require, SAACError
from .accounting import vector, dimensions, settlement, admission


def verify_tape(events, keys, expected_head=None):
    prev, seq, decisions, capabilities, receipts = 'genesis', 0, 0, 0, 0
    reservations = {}
    def totals():
        result = {}
        for r in reservations.values():
            for key, bound in r['bound'].items():
                value = result.setdefault(key, {'used': 0, 'reserved': 0})
                value['used'] += r['u'][key]
                value['reserved'] += bound-r['u'][key]-r['released'][key]
        return result
    for event in events:
        body = {k:v for k,v in event.items() if k != 'hash'}
        require(event['seq'] == seq+1 and event['prev_hash'] == prev and digest(body) == event['hash'], 'TAPE_INTEGRITY', 'Tape sequence or hash chain is broken.')
        seq, prev = event['seq'], event['hash']
        data, kind = event['data'], event['kind']
        if kind == 'POLICY_EVALUATED':
            require(evaluate(**data['inputs']) == data['decision'], 'TAPE_REPLAY', 'Committed inputs do not reproduce the verdict.')
            verify(data['inputs']['pack'], keys['riskbook-1'], 'riskbook-1', 'saac-pack')
            decisions += 1
        elif kind == 'RISK_RESERVED':
            risk, bound = dimensions(data['risk_before']), vector(data['bound'])
            reconstructed = totals()
            for key, value in risk.items():
                before = reconstructed.get(key, {'used': 0, 'reserved': 0})
                require(value['used'] == before['used'] and value['reserved'] == before['reserved'], 'TAPE_ACCOUNTING', 'Admission counters differ from reconstructed authoritative allocations.')
            admission(data['risk_before'], bound)
            require(data['id'] not in reservations, 'TAPE_RESERVATION', 'Reservation IDs must be unique.')
            reservations[data['id']] = {'bound': bound, 'u': {k:0 for k in bound}, 'released': {k:0 for k in bound}, 'revision': 0, 'kappa': None}
        elif kind == 'CAPABILITY_ISSUED':
            verify(data, keys['riskbook-1'], 'riskbook-1', 'saac-kappa')
            r = reservations.get(data['reservation']['id'])
            require(r is not None and r['bound'] == data['reservation']['bound'] and r['kappa'] is None, 'TAPE_RESERVATION', 'κ must follow one matching allocation.')
            require(digest(data['effect']) == data['effect_digest'], 'TAPE_EFFECT', 'Signed effect digest differs.')
            r['kappa'] = data
            capabilities += 1
        elif kind == 'RECEIPT_RECONCILED':
            rho = data['receipt']
            if '__audiences__' in keys:
                kid = keys['__audiences__'].get(rho['aud'])
                require(kid in keys, 'TAPE_OBSERVER', 'Audience has no accepted observer.')
                verify(rho, keys[kid], kid, 'saac-receipt')
            else:
                verify(rho, keys['socket-1'], 'socket-1', 'saac-receipt')
            r = reservations.get(rho['reservation_id'])
            require(r is not None and r['kappa'] is not None, 'TAPE_RECEIPT', 'Receipt must follow committed authority.')
            k = r['kappa']
            require(rho['snapshot_hash'] == k['snapshot_hash'] and rho['nonce'] == k['nonce']
                    and rho['kappa_id'] == k.get('kappa_id', k['nonce']) and rho['aud'] == k['aud'], 'TAPE_RECEIPT', 'Receipt bindings differ.')
            s = rho['settlement']
            require(s == settlement(r['bound'], rho['result']['consumed'], rho['result']['released']), 'TAPE_SETTLEMENT', 'Conservation failed.')
            for key in r['bound']:
                require(s['consumed'][key] >= r['u'][key] and s['released'][key] >= r['released'][key], 'TAPE_SETTLEMENT', 'Evidence regressed.')
                if 'budget_deltas' in data:
                    require(data['budget_deltas'][key] == {'used': s['consumed'][key]-r['u'][key], 'reserved': -(s['consumed'][key]-r['u'][key])-(s['released'][key]-r['released'][key])}, 'TAPE_DELTA', 'Budget deltas differ from evidence.')
            require(rho['revision'] > r['revision'], 'TAPE_SETTLEMENT', 'Applied evidence revisions must advance.')
            if 'amount_cents' in r['bound']:
                require(data['delta_u'] == s['consumed']['amount_cents']-r['u']['amount_cents'] and data['delta_q'] == -(s['consumed']['amount_cents']-r['u']['amount_cents'])-(s['released']['amount_cents']-r['released']['amount_cents']), 'TAPE_DELTA', 'Recorded deltas differ.')
            r.update(u=s['consumed'], released=s['released'], revision=rho['revision'])
            receipts += 1
    require(expected_head is None or prev == expected_head, 'TAPE_CHECKPOINT', 'Tape differs from the retained checkpoint.')
    total = totals()
    return {'valid': True, 'events': seq, 'decisions_replayed': decisions, 'capabilities_verified': capabilities,
            'receipts_verified': receipts, 'head': prev, 'used_cents': total.get('amount_cents', {}).get('used', 0),
            'reserved_cents': total.get('amount_cents', {}).get('reserved', 0), 'budgets': total,
            'unresolved': [rid for rid,r in reservations.items() if r['revision'] == 0 or any(r['bound'][k] > r['u'][k]+r['released'][k] for k in r['bound'])],
            'external_checkpoint': 'Compared to caller-supplied head; no independent external anchor.' if expected_head else 'Not externally anchored; a local chain alone cannot prove absence of truncation.'}

def main():
    import argparse
    import json
    import sys
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Replay an exported SAAC tape with its public keys")
    parser.add_argument("tape", type=Path)
    parser.add_argument("--expected-head", help="Head previously retained in an independent trust domain")
    args = parser.parse_args()
    bundle = json.loads(args.tape.read_text())
    try:
        result = verify_tape(bundle["events"], bundle["keys"], args.expected_head)
    except SAACError as error:
        print(json.dumps({"valid": False, "code": error.code, "message": error.message}))
        sys.exit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
