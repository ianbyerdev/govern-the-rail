"""Authoritative ownership and transaction boundary. No actor object is stored here."""
from contextlib import contextmanager
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4
from .crypto import canonical, digest


def uid(prefix): return f"{prefix}_{uuid4().hex[:16]}"
def encode(x): return canonical(x).decode()
def decode(x): return json.loads(x)


class RiskBook:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS request_keys (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, proposal_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS campaign_totals (unit TEXT PRIMARY KEY, used INTEGER NOT NULL, reserved INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS packs (hash TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL, nonce TEXT UNIQUE NOT NULL,
                    bound INTEGER NOT NULL CHECK(bound>=0), consumed INTEGER NOT NULL DEFAULT 0,
                    released INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    receipt_hash TEXT, kappa TEXT NOT NULL,
                    CHECK(consumed>=0 AND released>=0 AND consumed+released<=bound));
                CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, status TEXT NOT NULL, execution_id TEXT);
                CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, reservation_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    data TEXT NOT NULL, UNIQUE(reservation_id, revision));
                CREATE TABLE IF NOT EXISTS payments (execution_id TEXT PRIMARY KEY, beneficiary TEXT NOT NULL, amount_cents INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS allocations (reservation_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox (execution_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS unsafe_sink (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT NOT NULL,
                    ts INTEGER NOT NULL, data TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def state(self, db): return decode(db.execute("SELECT data FROM state WHERE id=1").fetchone()[0])
    def save_state(self, db, state): db.execute("INSERT OR REPLACE INTO state VALUES (1,?)", (encode(state),))
    def grant(self, db, gid):
        row = db.execute("SELECT data FROM grants WHERE id=?", (gid,)).fetchone()
        return decode(row[0]) if row else None
    def save_grant(self, db, grant): db.execute("INSERT OR REPLACE INTO grants VALUES (?,?)", (grant["id"], encode(grant)))
    def run(self, db, rid):
        row = db.execute("SELECT data FROM runs WHERE id=?", (rid,)).fetchone()
        return decode(row[0]) if row else None
    def save_run(self, db, run): db.execute("INSERT OR REPLACE INTO runs VALUES (?,?)", (run["id"], encode(run)))

    def risk(self, db):
        state = self.state(db)
        if state.get('profile', 'payments') != 'payments':
            budgets = {k: {'used': 0, 'reserved': 0, 'limit': v, 'available': v} for k, v in state['pack']['limits'].items()}
            if state.get('profile') == 'swarm':
                for row in db.execute('SELECT * FROM campaign_totals'):
                    budgets[row['unit']].update(used=row['used'], reserved=row['reserved'], available=budgets[row['unit']]['limit']-row['used']-row['reserved'])
                return {'budgets': budgets}
            for row in db.execute('SELECT data FROM allocations'):
                for key, value in decode(row[0]).items():
                    budgets[key]['used'] += value['consumed']
                    budgets[key]['reserved'] += value['bound']-value['consumed']-value['released']
            for value in budgets.values(): value['available'] = value['limit']-value['used']-value['reserved']
            return {'budgets': budgets}
        row = db.execute("SELECT COALESCE(SUM(consumed),0) u, COALESCE(SUM(bound-consumed-released),0) q FROM reservations").fetchone()
        limit = state["pack"]["session_limit_cents"]
        return {"used_cents": row["u"], "reserved_cents": row["q"], "limit_cents": limit,
                "available_cents": limit-row["u"]-row["q"]}

    def reserve_dimensions(self, db, rid, bound):
        if isinstance(bound, dict):
            for k, v in bound.items():
                db.execute('INSERT INTO campaign_totals VALUES (?,0,?) ON CONFLICT(unit) DO UPDATE SET reserved=reserved+excluded.reserved', (k,v))
            db.execute('INSERT INTO allocations VALUES (?,?)', (rid, encode({k: {'bound': v, 'consumed': 0, 'released': 0} for k, v in bound.items()})))

    def allocation(self, db, row):
        result = db.execute('SELECT data FROM allocations WHERE reservation_id=?', (row['id'],)).fetchone()
        return decode(result[0]) if result else {'amount_cents': {k: row[k] for k in ('bound', 'consumed', 'released')}}

    def settle_dimensions(self, db, row, settlement):
        if db.execute('SELECT 1 FROM allocations WHERE reservation_id=?', (row['id'],)).fetchone():
            values = self.allocation(db, row)
            for key in values:
                old = values[key]
                du = settlement['consumed'][key]-old['consumed']
                dq = -du-(settlement['released'][key]-old['released'])
                db.execute('UPDATE campaign_totals SET used=used+?,reserved=reserved+? WHERE unit=?', (du,dq,key))
                values[key].update(consumed=settlement['consumed'][key], released=settlement['released'][key])
            db.execute('UPDATE allocations SET data=? WHERE reservation_id=?', (encode(values), row['id']))

    def event(self, db, run_id, kind, data, now):
        last = db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (last["seq"]+1, last["hash"]) if last else (1, "genesis")
        record = {"seq": seq, "run_id": run_id, "kind": kind, "ts": now, "data": data, "prev_hash": prev}
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (seq, run_id, kind, now, encode(data), prev, digest(record)))
        return seq

    def events(self, db, run_id=None):
        rows = db.execute("SELECT * FROM events" + (" WHERE run_id=?" if run_id else "") + " ORDER BY seq", (run_id,) if run_id else ())
        return [{**dict(r), "data": decode(r["data"])} for r in rows]
