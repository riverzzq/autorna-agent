"""SQLite run log — AutoRNA Agent.

One table, no ORM. Every run the agent launches gets a row, including
failures: a failed run is a logged row with status='failed' and an
error message, never a silent gap and never a faked success.
"""
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    samples     TEXT NOT NULL,   -- JSON list of sample ids
    contrast    TEXT NOT NULL,   -- e.g. "RAP1_IAA_30M_vs_WT"
    genome      TEXT NOT NULL,
    status      TEXT NOT NULL,   -- queued | running | succeeded | failed
    wall_time_s REAL,
    resources   TEXT,            -- JSON: cpus / memory requested
    qc_pass     INTEGER,         -- 1 pass, 0 fail, NULL not yet checked
    error       TEXT,            -- failure message / error kind, NULL on success
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""


def connect(db_path=DEFAULT_DB):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_run(conn, run_id, samples, contrast, genome, resources=None):
    now = time.time()
    conn.execute(
        "INSERT INTO runs (id, samples, contrast, genome, status, resources, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
        (run_id, samples, contrast, genome, resources, now, now),
    )
    conn.commit()


def update_run(conn, run_id, **fields):
    """Update any of: status, wall_time_s, qc_pass, error, resources."""
    allowed = {"status", "wall_time_s", "qc_pass", "error", "resources"}
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"db.update_run: field not allowed: {key}")
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(time.time())
    values.append(run_id)
    conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()


def get_run(conn, run_id):
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs(conn, limit=50):
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
