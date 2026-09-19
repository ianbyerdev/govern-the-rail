"""Trusted supervisor for a fixed Bubblewrap job; no arbitrary shell or fallback.

A durable claim is never automatically retried. A crash after claim and before a
trusted exit journal strands capacity conservatively; durable queued intents can
be claimed once, and recorded exit evidence can be reconciled after restart.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from functools import lru_cache
from .models import require, SAACError
from .risk_book import decode, encode
from .crypto import digest

SCRIPT = Path(__file__).parent / 'fixtures/audit.py'


@lru_cache(maxsize=8)
def file_hash(path, stat_signature):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def executable_contract():
    paths = {'python': Path('/usr/bin/python3').resolve(), 'bubblewrap': Path(shutil.which('bwrap') or '/missing-bwrap')}
    result = {}
    for key, path in paths.items():
        stat = path.stat() if path.exists() else None
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) if stat else ()
        result[key] = {'path': str(path), 'sha256': file_hash(str(path), signature) if stat else 'unavailable'}
    return result


def runtime_contract():
    return {'version': 1, 'script_sha256': hashlib.sha256(SCRIPT.read_bytes()).hexdigest(), 'executables': executable_contract(),
            'isolation': 'bubblewrap-unshare-all', 'mount_policy': 1, 'environment': 'clear', 'host_runtime': 'trusted-read-only'}


def available():
    return bool(shutil.which('bwrap') and Path('/usr/bin/python3').exists())


class LocalRunner:
    def __init__(self, service): self.service = service

    def run(self, job_id, fault=None):
        svc = self.service
        require(available(), 'CONTAINMENT_UNAVAILABLE', 'Bubblewrap and system Python are required; no host-shell fallback is allowed.')
        folder = svc.directory / 'runner' / job_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        journal = folder / 'exit.json'
        with svc.book.transaction() as db:
            row = db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
            require(row is not None, 'UNKNOWN_JOB', 'This job has no committed dispatch intent.')
            job = decode(row[0])
            if job['status'] != 'queued':
                require(journal.exists(), 'OUTCOME_UNCERTAIN', 'The launch was claimed. It will not be repeated without accepted outcome evidence.')
                return self._read_journal(journal, job_id)
            from .protocol import verify_artifact as verify
            from .authority import live_grant
            from .resolver import resolve
            from .domain_models import parse_proposal
            reservation = db.execute('SELECT r.* FROM reservations r JOIN executions e ON e.reservation_id=r.id WHERE e.id=?', (job_id,)).fetchone()
            kappa = decode(reservation['kappa'])
            verify(kappa, svc.issuer.public, 'riskbook-1', 'saac-kappa')
            state, now = svc.book.state(db), svc.now()
            verify(state['pack'], svc.issuer.public, 'riskbook-1', 'saac-pack')
            require(digest(state['pack']) == state['pack_hash'], 'PACK_INTEGRITY', 'The live pack content changed without publication.')
            require(kappa['aud'] == 'RUNTIME-1', 'WRONG_AUDIENCE', 'Only runtime authority can launch a job.')
            nonce = db.execute('SELECT * FROM nonces WHERE nonce=?', (kappa['nonce'],)).fetchone()
            require(nonce['status'] == 'consumed' and nonce['execution_id'] == job_id, 'JOB_BINDING', 'Launch must bind the single redeemed intent.')
            require(kappa['iat'] <= now < kappa['exp'], 'EXPIRED', 'Dispatch authority expired before the runner claimed launch.')
            require(state['pack_hash'] == kappa['pack_hash'], 'PACK_NOT_LIVE', 'The issuing pack was superseded before launch.')
            require(not state['breaker'], 'BREAKER_HALT', 'The institution halted the queued launch.')
            run = svc.book.run(db, reservation['run_id'])
            proposal = parse_proposal(run['proposal'])
            grant = live_grant(svc.book, db, proposal.grant_id, now)
            require(resolve(proposal, state, grant, db).model_dump() == job['effect'], 'STALE_STATE', 'Queued job bindings changed before launch.')
            require(job['effect']['c']['runtime'] == runtime_contract(), 'RUNTIME_STALE', 'The runner code changed after authorization.')
            script_bytes = SCRIPT.read_bytes()
            require(hashlib.sha256(script_bytes).hexdigest() == job['effect']['c']['runtime']['script_sha256'], 'RUNTIME_STALE', 'The script changed while preparing its sealed copy.')
            job.update(status='claimed', launch_count=1)
            db.execute('UPDATE jobs SET data=? WHERE id=?', (encode(job), job_id))
            svc.book.event(db, reservation['run_id'], 'JOB_LAUNCH_CLAIMED', {'job_id': job_id, 'launch_count': 1}, svc.now())
        if fault == 'after_claim': raise SAACError('OUTCOME_UNCERTAIN', 'Injected interruption after durable launch claim; capacity remains held.')
        workspace = folder / 'workspace'
        workspace.mkdir(exist_ok=True)
        marker = folder / 'risk-marker'
        marker.write_text('Synthetic institution marker: read-only in the child.\n')
        sealed_script = folder / 'sealed-audit.py'
        sealed_script.write_bytes(script_bytes)
        args = [shutil.which('bwrap'), '--unshare-all', '--die-with-parent', '--new-session', '--clearenv', '--setenv', 'LANG', 'C.UTF-8',
                '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
                '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/institution', '--dir', '/task',
                '--ro-bind', str(marker.resolve()), '/institution/risk-marker', '--ro-bind', str(sealed_script.resolve()), '/task/audit.py',
                '--bind', str(workspace.resolve()), '/workspace', '--chdir', '/workspace', '--remount-ro', '/',
                '/usr/bin/python3', '-I', '/task/audit.py']
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False,
                                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            evidence = {'job_id': job_id, 'status': 'completed' if result.returncode == 0 else 'failed', 'returncode': result.returncode,
                        'stdout': result.stdout[:20000], 'stderr': result.stderr[:2000], 'launch_count': 1,
                        'containment': 'Bubblewrap: separate network/PID/user/mount namespaces; only scratch workspace writable'}
            if result.returncode == 0:
                evidence['probe_results'] = json.loads(result.stdout)
                artifact = workspace / 'report.txt'
                evidence['artifact_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except subprocess.TimeoutExpired:
            evidence = {'job_id': job_id, 'status': 'failed', 'returncode': -9, 'stderr': 'Runner timed out and killed the sandbox.', 'launch_count': 1}
        # Only the supervisor can write this journal; it is not mounted in the child.
        signed = svc.socket_signer.sign({'typ': 'saac-runner-exit', **evidence})
        temp = folder / 'exit.tmp'
        with temp.open('w') as handle:
            handle.write(encode(signed)); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, journal)
        directory_fd = os.open(folder, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
        if fault == 'after_run': raise SAACError('OUTCOME_UNCERTAIN', 'Exit is durably journaled; its receipt delivery was interrupted.')
        return signed

    def close_queued(self, job_id):
        """Accepted non-use proof: close an unclaimed intent under the claim lock."""
        svc = self.service
        with svc.book.transaction() as db:
            job = decode(db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
            require(job['status'] == 'queued' and job['launch_count'] == 0, 'OUTCOME_UNCERTAIN', 'A claimed job cannot be declared unused without exit evidence.')
            row = db.execute('SELECT r.* FROM reservations r JOIN executions e ON e.reservation_id=r.id WHERE e.id=?', (job_id,)).fetchone()
            execution = decode(db.execute('SELECT data FROM executions WHERE id=?', (job_id,)).fetchone()[0])
            execution['revision'] += 1
            bound = decode(row['kappa'])['reservation']['bound']
            execution['result'] = {'status': 'non_use', 'job_id': job_id, 'consumed': {k:0 for k in bound}, 'released': bound}
            job['status'] = 'cancelled'
            db.execute('UPDATE jobs SET data=? WHERE id=?', (encode(job), job_id))
            db.execute("UPDATE nonces SET status='closed' WHERE nonce=?", (row['nonce'],))
            svc.book.event(db, row['run_id'], 'JOB_NON_USE_PROVEN', {'job_id': job_id, 'launch_count': 0}, svc.now())
            return svc.socket._persist(db, row, execution, svc.now())

    def _read_journal(self, path, job_id):
        from .protocol import verify_artifact as verify
        value = json.loads(path.read_text())
        verify(value, self.service.socket_signer.public, 'socket-1', 'saac-runner-exit')
        require(value['job_id'] == job_id, 'JOB_BINDING', 'Exit evidence belongs to another job.')
        return value

    def finish(self, job_id, fault=None):
        evidence = self.run(job_id, fault)
        svc = self.service
        with svc.book.transaction() as db:
            row = db.execute('SELECT r.* FROM reservations r JOIN executions e ON r.id=e.reservation_id WHERE e.id=?', (job_id,)).fetchone()
            execution = decode(db.execute('SELECT data FROM executions WHERE id=?', (job_id,)).fetchone()[0])
            if execution['result']['status'] in ('completed', 'failed'):
                return decode(db.execute('SELECT data FROM receipts WHERE reservation_id=? ORDER BY revision DESC LIMIT 1', (row['id'],)).fetchone()[0])
            execution['revision'] += 1
            execution['result'] = {'status': evidence['status'], 'job_id': job_id, 'evidence': evidence,
                                   'consumed': {'job_starts': 1, 'job_slots': 0}, 'released': {'job_starts': 0, 'job_slots': 1}}
            job = decode(db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()[0])
            job.update(status=evidence['status'], evidence=evidence)
            db.execute('UPDATE jobs SET data=? WHERE id=?', (encode(job), job_id))
            svc.book.event(db, row['run_id'], 'JOB_EXIT_OBSERVED', evidence, svc.now())
            svc.book.event(db, row['run_id'], 'EXECUTION_COMPLETED', execution, svc.now())
            return svc.socket._persist(db, row, execution, svc.now())
