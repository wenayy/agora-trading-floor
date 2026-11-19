"""SQLite storage shared by the floor, the MCP servers and the API.

Everything goes through one database file in WAL mode, so MCP servers can run in-process or
as separate stdio subprocesses and still see the same accounts, prices and memory.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS securities (symbol TEXT PRIMARY KEY, name TEXT, sector TEXT, pe REAL, dividend_yield REAL,
    description TEXT);
CREATE TABLE IF NOT EXISTS prices (symbol TEXT, day INTEGER, price REAL, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS news (id INTEGER PRIMARY KEY AUTOINCREMENT, day INTEGER, symbol TEXT, headline TEXT,
    body TEXT, sentiment REAL);
CREATE TABLE IF NOT EXISTS accounts (trader_id TEXT PRIMARY KEY, name TEXT, strategy TEXT, cash REAL,
    starting_cash REAL);
CREATE TABLE IF NOT EXISTS holdings (trader_id TEXT, symbol TEXT, qty INTEGER, avg_cost REAL,
    PRIMARY KEY (trader_id, symbol));
CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, trader_id TEXT, day INTEGER,
    side TEXT, symbol TEXT, qty INTEGER, price REAL, fee REAL, rationale TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS snapshots (trader_id TEXT, day INTEGER, cash REAL, equity REAL,
    PRIMARY KEY (trader_id, day));
CREATE TABLE IF NOT EXISTS memory (trader_id TEXT, key TEXT, value TEXT, day INTEGER, ts REAL,
    PRIMARY KEY (trader_id, key));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, day INTEGER, trader_id TEXT, kind TEXT,
    data TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS idx_events_trader ON events (trader_id, id);
CREATE INDEX IF NOT EXISTS idx_tx_trader ON transactions (trader_id, id);
"""


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ helpers
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self.conn.execute(sql, args).lastrowid

    def tx(self):
        """``with db.tx():`` runs a block atomically (BEGIN IMMEDIATE ... COMMIT/ROLLBACK)."""
        db = self

        class _Tx:
            def __enter__(self_inner):
                db._lock.acquire()
                db.conn.execute("BEGIN IMMEDIATE")

            def __exit__(self_inner, exc_type, *_):
                try:
                    db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    db._lock.release()
                return False

        return _Tx()

    # ------------------------------------------------------------------ meta / events
    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        self.x("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    def today(self) -> int:
        return int(self.get_meta("day", 0))

    def log_event(self, trader_id: str | None, kind: str, data: dict) -> int:
        return self.x("INSERT INTO events (day, trader_id, kind, data, ts) VALUES (?,?,?,?,?)",
                      (self.today(), trader_id, kind, json.dumps(data, default=str), time.time()))
