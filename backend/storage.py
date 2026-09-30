"""Each process owns one database. Short transactions never span an await."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

EVENTS = [
    {"id": "neon", "name": "Neon Nights", "genre": "ELECTRÓNICA", "date": "24 OCT", "venue": "Costa Salguero", "price": 28000, "stock": 200},
    {"id": "indie", "name": "Indie al Parque", "genre": "INDIE / ALTERNATIVO", "date": "07 NOV", "venue": "Ciudad Cultural Konex", "price": 18500, "stock": 200},
    {"id": "jazz", "name": "Blue Sessions", "genre": "JAZZ / SOUL", "date": "21 NOV", "venue": "Niceto Club", "price": 22000, "stock": 200},
]


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS inventory(id TEXT PRIMARY KEY, stock INTEGER NOT NULL CHECK(stock >= 0));
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, event_id TEXT, amount INTEGER, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS purchases(id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, http_code INTEGER NOT NULL DEFAULT 200);
                CREATE TABLE IF NOT EXISTS timeline(seq INTEGER PRIMARY KEY AUTOINCREMENT, purchase_id TEXT, step TEXT, status TEXT, message TEXT, at TEXT);
            """)
            for event in EVENTS:
                db.execute("INSERT OR IGNORE INTO inventory VALUES (?, ?)", (event["id"], event["stock"]))

    @contextmanager
    def connect(self):
        # One event-loop thread per service; no caller awaits inside a transaction.
        # Keep WAL handles open instead of repeatedly creating/deleting them.
        with self.db:
            yield self.db

    def operation(self, purchase_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (purchase_id,)).fetchone()
            return dict(row) if row else None

    def catalog(self):
        with self.connect() as db:
            stocks = dict(db.execute("SELECT id, stock FROM inventory"))
        return [{**event, "stock": stocks[event["id"]]} for event in EVENTS]

    def create_purchase(self, purchase_id, body):
        with self.connect() as db:
            db.execute("INSERT INTO purchases(id,body,status,created_at) VALUES (?, ?, 'running', ?)", (purchase_id, json.dumps(body), now()))

    def status(self, purchase_id, status, code=200):
        with self.connect() as db:
            db.execute("UPDATE purchases SET status=?, http_code=? WHERE id=?", (status, code, purchase_id))

    def log(self, purchase_id, step, status, message):
        with self.connect() as db:
            db.execute("INSERT INTO timeline(purchase_id,step,status,message,at) VALUES (?,?,?,?,?)", (purchase_id, step, status, message, now()))

    def purchase(self, purchase_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["body"] = json.loads(result["body"])
            result["events"] = [dict(r) for r in db.execute("SELECT * FROM timeline WHERE purchase_id=? ORDER BY seq", (purchase_id,))]
            return result

    def purchases(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM purchases ORDER BY created_at DESC LIMIT 100")]
        return [self.purchase(i) for i in ids]

    def reset(self):
        with self.connect() as db:
            for table in ("operations", "purchases", "timeline"):
                db.execute(f"DELETE FROM {table}")
            for event in EVENTS:
                db.execute("UPDATE inventory SET stock=? WHERE id=?", (event["stock"], event["id"]))
