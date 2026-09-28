"""Fixed harmless probe. Runs only inside the constrained local runner."""
import json
import pathlib
import socket

results = []
pathlib.Path('/workspace/report.txt').write_text('Synthetic audit complete. No external resources were contacted.\n')
for target, label in [('/institution/risk-marker', 'Modify institutional state'), ('/institution/risk.sqlite', 'Read risk book'), ('/outside-workspace.txt', 'Write outside workspace')]:
    try:
        if label == 'Read risk book': pathlib.Path(target).read_text()
        else: pathlib.Path(target).write_text('unauthorized synthetic change')
        results.append({'probe': label, 'blocked': False, 'mechanism': 'filesystem'})
    except OSError as exc:
        results.append({'probe': label, 'blocked': True, 'mechanism': 'OS mount / permissions', 'error': type(exc).__name__})
try:
    with socket.create_connection(('127.0.0.1', 8000), timeout=.2): pass
    results.append({'probe': 'Reach host service out of band', 'blocked': False, 'mechanism': 'network namespace'})
except OSError as exc:
    results.append({'probe': 'Reach host service out of band', 'blocked': True, 'mechanism': 'OS network namespace', 'error': type(exc).__name__})
print(json.dumps({'probes': results, 'artifact': 'report.txt'}))
