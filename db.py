"""SQLite-хранилище. Один файл, WAL, потокобезопасно."""
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "afisha.db"

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    description TEXT,
    starts_at   TEXT,
    date_text   TEXT,
    place       TEXT,
    age_group   TEXT,
    price       REAL,
    price_type  TEXT,
    capacity    INTEGER,
    source_url  TEXT,
    image_url   TEXT,
    raw_json    TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    platform         TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    name             TEXT,
    chat_id          TEXT,
    created_at       TEXT,
    updated_at       TEXT,
    UNIQUE(platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS registrations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id),
    event_id          INTEGER NOT NULL REFERENCES events(id),
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TEXT,
    cancelled_at      TEXT,
    reminder_sent_at  TEXT,
    UNIQUE(user_id, event_id)
);

CREATE TABLE IF NOT EXISTS parser_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT,
    finished_at    TEXT,
    status         TEXT,
    events_fetched INTEGER,
    error          TEXT
);
"""

# Лёгкие миграции для баз, созданных до появления этих колонок.
_MIGRATIONS = [
    "ALTER TABLE parser_runs ADD COLUMN error TEXT",
]


def init() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # колонка уже существует
    conn.commit()