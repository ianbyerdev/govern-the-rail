"""Bounded anonymous workspaces around the existing authority and domain APIs.

The cookie identifies a visitor workspace, never an institution operator. No
bearer token, signing key or adapter-specific authority is sent to the browser.
This small host intentionally runs in one process; a file lock enforces that.
"""
from contextlib import contextmanager, closing
from dataclasses import dataclass
import fcntl
import hashlib
from pathlib import Path
import secrets
import shutil
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .config import setting
from .service import SAACService

COOKIE = "saac_demo"
PREFIX = "/api/demo"


@dataclass(frozen=True)
class DemoLimits:
    lifetime_seconds: int = 3600
    sessions: int = 16
    books: int = 12
    campaigns: int = 4
    population: int = 100
    concurrent_jobs: int = 2
    jobs_per_session: int = 12
    writes_per_session: int = 240
    writes_per_minute: int = 30
    requests_per_minute: int = 240
    starts_per_ip: int = 10  # Ten-minute window; use the trusted peer address.
    starts_per_minute: int = 30
    body_bytes: int = 32768
    workspace_bytes: int = 32 * 1024 * 1024

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError('Demo limits must be positive integers.')

    @classmethod
    def configured(cls):
        return cls(lifetime_seconds=int(setting('DEMO_LIFETIME_SECONDS', '3600')),
                   sessions=int(setting('DEMO_MAX_SESSIONS', '16')),
                   concurrent_jobs=int(setting('DEMO_CONCURRENT_JOBS', '2')))

    def public(self):
        return {"lifetime_seconds": self.lifetime_seconds, "max_books": self.books,
                "max_campaigns": self.campaigns, "max_population": self.population,
                "max_heavy_jobs": self.jobs_per_session, "contained_workers": False}


def refuse(code, message, status=429):
    raise HTTPException(status, {"code": code, "message": message})


class DemoWorkspace:
    def __init__(self, manager, identifier, expires_at):
        from .workbench import ExperimentStore
        from .swarm.campaign import CampaignStore
        self.manager, self.id, self.expires_at = manager, identifier, expires_at
        self.directory = manager.directory / identifier
        self.active = self.jobs = 0
        self.main = SAACService(self.directory)
        self.experiments = ExperimentStore(self.directory, self.main, demo=self)
        self.campaigns = CampaignStore(self.directory, demo=self)

    def heavy(self, budget=True):
        return self.manager.heavy(self, budget=budget)

    def start_campaign(self, campaign, count=None):
        # Acquire before returning HTTP success. Keep the lease until the actual
        # background work ends, so polling cannot make another slot available.
        lease = self.heavy()
        lease.__enter__()
        def run():
            try: campaign.run(count)
            finally: lease.__exit__(None, None, None)
        try:
            campaign.thread = threading.Thread(target=run, daemon=True)
            campaign.thread.start()
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def stop(self):
        for campaign in list(self.campaigns.cache.values()): campaign.stop.set()


class DemoManager:
    def __init__(self, directory, limits=None, clock=time.time, origin=None):
        self.directory = Path(directory) / "visitors"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limits, self.clock = limits or DemoLimits.configured(), clock
        self.origin = (origin or setting("PUBLIC_ORIGIN", "")).rstrip("/")
        if self.origin:
            parsed = urlsplit(self.origin)
            if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
                raise ValueError("SAAC_PUBLIC_ORIGIN must be an http(s) origin without a path.")
        self.lock, self.workspaces = threading.RLock(), {}
        self.jobs, self.host_lock, self.closed = 0, None, False
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    expires_at INTEGER NOT NULL, writes INTEGER NOT NULL DEFAULT 0,
                    jobs INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS rates (name TEXT PRIMARY KEY, started INTEGER NOT NULL, count INTEGER NOT NULL);
            ''')

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.directory / "sessions.sqlite", timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db: yield db

    def start(self):
        with self.lock:
            if self.host_lock: return
            if self.closed: refuse('DEMO_OFFLINE', 'The demo is restarting. Please try again.', 503)
            handle = (self.directory / "host.lock").open("a")
            try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                refuse('DEMO_HOST', 'Public workspaces require one server process per data directory.', 503)
            self.host_lock = handle

    def rate(self, db, name, maximum, period):
        now = int(self.clock())
        row = db.execute('SELECT * FROM rates WHERE name=?', (name,)).fetchone()
        if row and now < row['started'] + period:
            if row['count'] >= maximum: refuse('DEMO_RATE_LIMIT', 'Please wait before trying again.')
            db.execute('UPDATE rates SET count=count+1 WHERE name=?', (name,))
        else:
            db.execute('INSERT OR REPLACE INTO rates VALUES (?,?,1)', (name, now))

    def lookup(self, db, token):
        if not token or len(token) > 128: return None
        return db.execute('SELECT * FROM sessions WHERE token_hash=?',
                          (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()

    def valid(self, row):
        return row is not None and not row['revoked'] and row['expires_at'] > self.clock()

    def describe(self, row):
        return {"active": True, "mode": "visitor", "session_id": row['id'],
                "expires_at": row['expires_at'], "limits": self.limits.public()}

    def status(self, token):
        self.start()
        with self.lock, self.db() as db:
            row = self.lookup(db, token)
            return self.describe(row) if self.valid(row) else {"active": False, "limits": self.limits.public()}

    def workspace(self, row):
        if row['id'] not in self.workspaces:
            self.workspaces[row['id']] = DemoWorkspace(self, row['id'], row['expires_at'])
        return self.workspaces[row['id']]

    def create(self, old_token, address):
        self.start()
        with self.lock:
            self.cleanup()
            with self.db() as db:
                old = self.lookup(db, old_token)
                if self.valid(old): return old_token, self.describe(old)
                self.rate(db, 'start:global', self.limits.starts_per_minute, 60)
                self.rate(db, 'start:peer:' + hashlib.sha256(address.encode()).hexdigest(), self.limits.starts_per_ip, 600)
                if db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] >= self.limits.sessions:
                    refuse('DEMO_FULL', 'All demo spaces are in use. Please try again shortly.', 503)
                identifier, token = secrets.token_hex(16), secrets.token_urlsafe(32)
                expires = int(self.clock()) + self.limits.lifetime_seconds
                db.execute('INSERT INTO sessions(id,token_hash,expires_at) VALUES (?,?,?)',
                           (identifier, hashlib.sha256(token.encode()).hexdigest(), expires))
                row = self.lookup(db, token)
                try: self.workspace(row)
                except BaseException:
                    shutil.rmtree(self.directory / identifier, ignore_errors=True)
                    raise
                return token, self.describe(row)

    def enter(self, token, mutation):
        self.start()
        with self.lock:
            # Charge attempts even when a later resource check refuses them.
            # Rolling these counters back would let repeated failures evade limits.
            with self.db() as db:
                row = self.lookup(db, token)
                if not self.valid(row): refuse('DEMO_EXPIRED', 'Your demo session has ended. Start a new demo to continue.', 401)
                self.rate(db, 'request:' + row['id'], self.limits.requests_per_minute, 60)
                if mutation: self.rate(db, 'write:' + row['id'], self.limits.writes_per_minute, 60)
            workspace = self.workspace(row)
            if workspace.active >= 8: refuse('DEMO_BUSY', 'Wait for the current requests to finish.')
            if mutation:
                if row['writes'] >= self.limits.writes_per_session:
                    refuse('DEMO_LIMIT', 'This session has reached its action limit. Export your evidence and end the demo.')
                size = 0
                for path in workspace.directory.rglob('*'):
                    try:
                        if path.is_file(): size += path.stat().st_size
                    except FileNotFoundError:
                        pass  # SQLite may remove a transient journal during a running campaign.
                if size >= self.limits.workspace_bytes:
                    refuse('DEMO_STORAGE', 'This workspace is full. Export your evidence and end the demo.')
                with self.db() as db: db.execute('UPDATE sessions SET writes=writes+1 WHERE id=?', (row['id'],))
            workspace.active += 1
            return workspace

    def leave(self, workspace):
        with self.lock: workspace.active -= 1

    @contextmanager
    def heavy(self, workspace, budget=True):
        with self.lock, self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=?', (workspace.id,)).fetchone()
            if not self.valid(row): refuse('DEMO_EXPIRED', 'Your demo session has ended.', 401)
            if workspace.jobs or self.jobs >= self.limits.concurrent_jobs:
                refuse('DEMO_BUSY', 'A swarm is already running. Please try again when it finishes.')
            if budget and row['jobs'] >= self.limits.jobs_per_session:
                refuse('DEMO_LIMIT', 'This session has reached its swarm limit. Export your evidence and end the demo.')
            if budget: db.execute('UPDATE sessions SET jobs=jobs+1 WHERE id=?', (workspace.id,))
            self.jobs += 1
            workspace.jobs += 1
        try: yield
        finally:
            with self.lock:
                self.jobs -= 1
                workspace.jobs -= 1

    def end(self, token):
        with self.lock, self.db() as db:
            row = self.lookup(db, token)
            if row:
                db.execute('UPDATE sessions SET revoked=1 WHERE id=?', (row['id'],))
                if row['id'] in self.workspaces: self.workspaces[row['id']].stop()

    def cleanup(self):
        with self.lock, self.db() as db:
            rows = db.execute('SELECT * FROM sessions WHERE revoked=1 OR expires_at<=?', (int(self.clock()),)).fetchall()
            for row in rows:
                workspace = self.workspaces.get(row['id'])
                if workspace:
                    workspace.stop()
                    if workspace.active or workspace.jobs: continue
                folder = self.directory / row['id']
                if folder.exists(): shutil.rmtree(folder)
                self.workspaces.pop(row['id'], None)
                db.execute('DELETE FROM sessions WHERE id=?', (row['id'],))
            db.execute('DELETE FROM rates WHERE started<?', (int(self.clock()) - 600,))

    def close(self):
        with self.lock:
            self.closed = True
            workspaces = list(self.workspaces.values())
            for workspace in workspaces: workspace.stop()
        for workspace in workspaces:
            for campaign in list(workspace.campaigns.cache.values()):
                if campaign.thread: campaign.thread.join()  # Bounded to 100 synthetic actors; drain before unlocking storage.
        with self.lock:
            self.cleanup()
            if self.host_lock:
                self.host_lock.close()
                self.host_lock = None


def demo_workspace(request: Request):
    # Only middleware can install the authenticated workspace. No ID from a
    # path, JSON body, header or query selects another visitor's storage.
    value = getattr(request.state, 'demo_workspace', None)
    if value is None: refuse('DEMO_SESSION', 'Start a demo first.', 401)
    return value


def demo_experiments(request: Request): return demo_workspace(request).experiments
def demo_campaigns(request: Request): return demo_workspace(request).campaigns


class DemoGuard:
    """Bound request bodies, pin authenticated workspaces and enforce CSRF checks."""
    def __init__(self, app, manager): self.app, self.manager = app, manager

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http': return await self.app(scope, receive, send)
        request = Request(scope)
        public = scope['path'] == PREFIX or scope['path'].startswith(PREFIX + '/')
        workspace = None
        async def safe_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                headers += [(b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                            (b'referrer-policy', b'no-referrer')]
                if scope['path'].startswith('/api/') or scope['path'] == '/':
                    headers += [(b'cache-control', b'no-store')]
                if scope['path'] == '/':
                    headers += [(b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]
                message = {**message, 'headers': headers}
            await send(message)
        try:
            if public:
                mutation = scope['method'] not in ('GET', 'HEAD', 'OPTIONS')
                if mutation:
                    expected = self.manager.origin or str(request.base_url).rstrip('/')
                    origin = request.headers.get('origin')
                    if (request.headers.get('x-saac-demo') != '1' or
                            request.headers.get('sec-fetch-site') == 'cross-site' or
                            origin is not None and origin != expected):
                        refuse('DEMO_ORIGIN', 'Start the demo from its own website.', 403)
            if scope['path'].startswith('/api/'):
                # Bound all API envelopes, including unauthenticated attempts at
                # administrator routes, before FastAPI can parse their JSON.
                body = bytearray()
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect': return
                    body.extend(message.get('body', b''))
                    if len(body) > self.manager.limits.body_bytes:
                        refuse('DEMO_BODY_LIMIT', 'This request is too large for the public demo.', 413)
                    if not message.get('more_body'): break
                async def buffered_receive():
                    nonlocal body
                    if body is not None:
                        result, body = bytes(body), None
                        return {'type': 'http.request', 'body': result, 'more_body': False}
                    return await receive()
                if public and scope['path'] != PREFIX + '/session':
                    workspace = await run_in_threadpool(self.manager.enter, request.cookies.get(COOKIE), mutation)
                    scope.setdefault('state', {})['demo_workspace'] = workspace
                return await self.app(scope, buffered_receive, safe_send)
            return await self.app(scope, receive, safe_send)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {'message': exc.detail}
            headers = {'Retry-After': '60'} if exc.status_code in (429, 503) else None
            await JSONResponse(detail, status_code=exc.status_code, headers=headers)(scope, receive, safe_send)
        finally:
            if workspace: await run_in_threadpool(self.manager.leave, workspace)


def register(app, directory, approval, clock_request, breaker, limits=None):
    manager = DemoManager(directory, limits=limits)
    app.state.demo_manager = manager
    app.add_middleware(DemoGuard, manager=manager)

    @app.get(PREFIX + '/session')
    def session(request: Request):
        result = manager.status(request.cookies.get(COOKIE))
        response = JSONResponse(result)
        if not result['active']: response.delete_cookie(COOKIE, path=PREFIX)
        return response

    @app.post(PREFIX + '/session')
    def start(request: Request):
        token, result = manager.create(request.cookies.get(COOKIE), request.client.host if request.client else 'unknown')
        response = JSONResponse(result)
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict', path=PREFIX,
                            secure=manager.origin.startswith('https://') or request.url.scheme == 'https',
                            max_age=max(1, result['expires_at'] - int(manager.clock())))
        return response

    @app.delete(PREFIX + '/session')
    def end(request: Request):
        manager.end(request.cookies.get(COOKIE))
        manager.cleanup()
        response = JSONResponse({'active': False})
        response.delete_cookie(COOKIE, path=PREFIX)
        return response

    from .workbench import register as workbench
    from .swarm.api import register as swarm
    workbench(app, directory, None, demo_workspace, approval, clock_request, breaker,
              prefix=PREFIX + '/workbench', store_dependency=demo_experiments)
    swarm(app, directory, demo_workspace, prefix=PREFIX + '/swarm', store_dependency=demo_campaigns)
    return manager
