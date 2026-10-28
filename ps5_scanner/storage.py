"""SQLite-Speicher: gesehene Inserate, Benachrichtigungen, Marktpreise, Kontaktanfragen."""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from dataclasses import asdict
from pathlib import Path

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    ad_id TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    title TEXT,
    price INTEGER,
    url TEXT,
    model TEXT,
    score REAL,
    status TEXT,              -- seen / rejected / candidate / notified / digest / skipped_ai / ignored / contacted
    reason TEXT,
    message_id INTEGER,
    data TEXT                 -- JSON: Listing + Analyse + KI
);
CREATE TABLE IF NOT EXISTS queries (
    name TEXT PRIMARY KEY,
    initialized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS market (
    ad_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    price INTEGER NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    ad_id TEXT,
    ts REAL,
    message TEXT,
    ok INTEGER,
    info TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status, first_seen);
"""


class Storage:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # --- Abfragen-Initialisierung (erster Lauf soll nicht alles melden) ---
    def query_initialized(self, name: str) -> bool:
        row = self.db.execute("SELECT initialized FROM queries WHERE name=?", (name,)).fetchone()
        return bool(row and row["initialized"])

    def mark_query_initialized(self, name: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO queries(name, initialized) VALUES (?, 1)", (name,))
        self.db.commit()

    # --- Inserate ---
    def get(self, ad_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM listings WHERE ad_id=?", (ad_id,)).fetchone()

    def known_ids(self, ad_ids: list[str]) -> dict[str, sqlite3.Row]:
        if not ad_ids:
            return {}
        q = ",".join("?" * len(ad_ids))
        rows = self.db.execute(f"SELECT ad_id, price, status FROM listings WHERE ad_id IN ({q})", ad_ids).fetchall()
        return {r["ad_id"]: r for r in rows}

    def touch(self, ad_ids: list[str]) -> None:
        now = time.time()
        self.db.executemany("UPDATE listings SET last_seen=? WHERE ad_id=?", [(now, i) for i in ad_ids])
        self.db.commit()

    def upsert(self, listing: Listing, status: str, *, model: str | None = None, score: float | None = None,
               reason: str | None = None, extra: dict | None = None) -> None:
        now = time.time()
        data = {"listing": asdict(listing)}
        if extra:
            data.update(extra)
        self.db.execute(
            """INSERT INTO listings(ad_id, first_seen, last_seen, title, price, url, model, score, status, reason, data)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ad_id) DO UPDATE SET last_seen=excluded.last_seen, title=excluded.title,
                 price=excluded.price, model=COALESCE(excluded.model, model), score=COALESCE(excluded.score, score),
                 status=excluded.status, reason=excluded.reason, data=excluded.data""",
            (listing.ad_id, now, now, listing.title, listing.price, listing.url, model, score, status, reason,
             json.dumps(data, ensure_ascii=False, default=str)),
        )
        self.db.commit()

    def set_status(self, ad_id: str, status: str, message_id: int | None = None) -> None:
        if message_id is not None:
            self.db.execute("UPDATE listings SET status=?, message_id=? WHERE ad_id=?", (status, message_id, ad_id))
        else:
            self.db.execute("UPDATE listings SET status=? WHERE ad_id=?", (status, ad_id))
        self.db.commit()

    def load_data(self, ad_id: str) -> dict | None:
        row = self.get(ad_id)
        return json.loads(row["data"]) if row and row["data"] else None

    def digest_candidates(self, since: float, limit: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM listings WHERE status='digest' AND first_seen>=? ORDER BY score DESC LIMIT ?",
            (since, limit),
        ).fetchall()

    def recent_notified(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM listings WHERE status IN ('notified','contacted') ORDER BY first_seen DESC LIMIT ?", (limit,)
        ).fetchall()

    def stats(self, since: float) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT status, COUNT(*) c FROM listings WHERE first_seen>=? GROUP BY status", (since,)
        ).fetchall()
        return {r["status"]: r["c"] for r in rows}

    # --- Marktpreise ---
    def add_market_price(self, ad_id: str, model: str, price: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO market(ad_id, model, price, ts) VALUES (?,?,?,?)",
                        (ad_id, model, price, time.time()))
        self.db.commit()

    def market_prices(self, model: str, days: float = 10) -> list[int]:
        since = time.time() - days * 86400
        return [r["price"] for r in self.db.execute(
            "SELECT price FROM market WHERE model=? AND ts>=?", (model, since)).fetchall()]

    def market_median(self, model: str, days: float = 10) -> tuple[float | None, int]:
        prices = self.market_prices(model, days)
        if not prices:
            return None, 0
        return statistics.median(prices), len(prices)

    # --- Kontakte ---
    def log_contact(self, ad_id: str, message: str, ok: bool, info: str = "") -> None:
        self.db.execute("INSERT INTO contacts(ad_id, ts, message, ok, info) VALUES (?,?,?,?,?)",
                        (ad_id, time.time(), message, int(ok), info))
        self.db.commit()

    def contacts_since(self, since: float) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM contacts WHERE ts>=? AND ok=1", (since,)).fetchone()["c"]

    def already_contacted(self, ad_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM contacts WHERE ad_id=? AND ok=1", (ad_id,)).fetchone() is not None

    # --- Key/Value ---
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)", (key, value))
        self.db.commit()

    def cleanup(self, days: int = 30) -> None:
        cutoff = time.time() - days * 86400
        self.db.execute("DELETE FROM listings WHERE last_seen<? AND status IN ('seen','rejected')", (cutoff,))
        self.db.execute("DELETE FROM market WHERE ts<?", (cutoff,))
        self.db.commit()
