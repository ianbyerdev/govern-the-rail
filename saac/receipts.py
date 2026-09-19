from .risk_book import uid
from .accounting import settlement


def make_receipt(signer, kappa, execution, now):
    result = execution['result']
    totals = settlement(kappa['reservation']['bound'], result['consumed'], result['released'])
    return signer.sign({'typ': 'saac-receipt', 'receipt_id': uid('receipt'), 'aud': kappa['aud'],
        'snapshot_hash': kappa['snapshot_hash'], 'kappa_id': kappa.get('kappa_id', kappa['nonce']), 'nonce': kappa['nonce'],
        'reservation_id': kappa['reservation']['id'], 'execution_id': execution['id'], 'revision': execution['revision'],
        'result': result, 'settlement': totals, 'ts': now})
