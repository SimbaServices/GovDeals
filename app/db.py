from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    auction_id INTEGER,
    watch_category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    url TEXT NOT NULL,
    make TEXT,
    model TEXT,
    year TEXT,
    location_city TEXT,
    location_state TEXT,
    seller TEXT,
    current_price REAL,
    bid_increment REAL,
    bid_count INTEGER,
    has_reserve INTEGER DEFAULT 0,
    reserve_not_met INTEGER DEFAULT 0,
    time_remaining TEXT,
    auction_start TEXT,
    auction_end TEXT,
    auction_end_utc TEXT,
    final_price REAL,
    status TEXT NOT NULL DEFAULT 'active',
    photo_url TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    hooks_assigned INTEGER NOT NULL DEFAULT 0,
    UNIQUE(asset_id, account_id, auction_id)
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    price REAL,
    time_remaining TEXT,
    minutes_remaining INTEGER,
    source TEXT NOT NULL,
    hook_id INTEGER,
    scraped_at TEXT NOT NULL,
    FOREIGN KEY(listing_id) REFERENCES listings(id),
    FOREIGN KEY(hook_id) REFERENCES auction_hooks(id)
);

CREATE TABLE IF NOT EXISTS auction_hooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL,
    minutes_before_end INTEGER NOT NULL,
    hook_name TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    fired_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    price REAL,
    time_remaining TEXT,
    error TEXT,
    UNIQUE(listing_id, minutes_before_end),
    FOREIGN KEY(listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    listings_found INTEGER DEFAULT 0,
    listings_upserted INTEGER DEFAULT 0,
    hooks_assigned INTEGER DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_category ON listings(watch_category);
CREATE INDEX IF NOT EXISTS idx_listings_end ON listings(auction_end_utc);
CREATE INDEX IF NOT EXISTS idx_hooks_status ON auction_hooks(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_listing ON price_snapshots(listing_id, scraped_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
