"""Anonymous demo workspaces, scoped storage and lazy expiration."""
import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from time import time as now

ROOT = Path(__file__).resolve().parents[1]
COOKIE_NAME = "energypilot_workspace"
WORKSPACE_TTL_SECONDS = 24 * 60 * 60
MAX_WORKSPACES = 200
MAX_READINGS = 10000


class WorkspaceCapacityError(Exception):
    pass


@contextmanager
def connect():
    path = Path(os.environ.get("ENERGYPILOT_DB", str(ROOT / "data" / "energy.sqlite3")))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, last_seen REAL NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS workspace_readings(
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        timestamp TEXT NOT NULL,
        kwh REAL NOT NULL CHECK(kwh>=0),
        PRIMARY KEY(workspace_id, timestamp))""")
    db.execute("CREATE INDEX IF NOT EXISTS workspace_expiry ON workspaces(last_seen)")
    try:
        with db:
            yield db
    finally:
        db.close()


def cleanup_expired(db, timestamp):
    # Foreign-key cascade removes readings with the expired workspace.
    db.execute("DELETE FROM workspaces WHERE last_seen <= ?", (timestamp - WORKSPACE_TTL_SECONDS,))


def resolve_workspace(cookie, seed_rows):
    timestamp = now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cleanup_expired(db, timestamp)
        if cookie and re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie):
            workspace_id = hashlib.sha256(cookie.encode()).hexdigest()
            if db.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone():
                db.execute("UPDATE workspaces SET last_seen=? WHERE id=?", (timestamp, workspace_id))
                return cookie, workspace_id
        count = db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
        if count >= MAX_WORKSPACES:
            raise WorkspaceCapacityError("Demo is busy. Please try again later.")
        cookie = secrets.token_urlsafe(32)
        workspace_id = hashlib.sha256(cookie.encode()).hexdigest()
        db.execute("INSERT INTO workspaces VALUES (?, ?)", (workspace_id, timestamp))
        db.executemany("INSERT INTO workspace_readings VALUES (?, ?, ?)",
                       ((workspace_id, stamp, kwh) for stamp, kwh in seed_rows))
        return cookie, workspace_id
