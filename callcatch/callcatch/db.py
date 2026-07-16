# -*- coding: utf-8 -*-
"""SQLite-хранилище. Времена — ISO-строки 'YYYY-MM-DD HH:MM'."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL,
    at TEXT NOT NULL,
    status TEXT NOT NULL,              -- answered | missed | afterhours
    source TEXT DEFAULT 'webhook'      -- webhook | sim
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL,
    call_id INTEGER REFERENCES calls(id),
    created_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'awaiting_reply',
    -- awaiting_reply | ask_service | ask_time | confirm | booked | declined | handoff
    service TEXT,                      -- ключ из config.SERVICES
    slot_proposed TEXT,                -- ISO предложенного слота (в состоянии confirm)
    fail_count INTEGER DEFAULT 0,
    last_activity TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    direction TEXT NOT NULL,           -- out | in
    text TEXT NOT NULL,
    at TEXT NOT NULL,
    channel TEXT DEFAULT 'sms'         -- sms | voice
);
CREATE TABLE IF NOT EXISTS voice_sessions (
    id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL,
    lead_id INTEGER REFERENCES leads(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_sec INTEGER DEFAULT 0,
    turns INTEGER DEFAULT 0,
    asked_human INTEGER DEFAULT 0,
    state TEXT DEFAULT 'active',       -- active | done
    outcome TEXT,                      -- booked | handoff | declined | limited | abandoned
    cost_est REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    phone TEXT NOT NULL,
    service TEXT NOT NULL,
    slot TEXT NOT NULL,                -- ISO начала слота
    price_est REAL NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT DEFAULT 'new'          -- new | visited | no_show
);
CREATE TABLE IF NOT EXISTS admin_notes (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    text TEXT NOT NULL,
    delivered TEXT DEFAULT 'log'       -- log | telegram
);
CREATE TABLE IF NOT EXISTS audit_calls (
    id INTEGER PRIMARY KEY,
    target TEXT NOT NULL,              -- название сервиса-прокта
    at TEXT NOT NULL,
    result TEXT NOT NULL,              -- answered | no_answer | busy | voicemail
    note TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:  # миграция старых баз, созданных до голосовой версии
        conn.execute("ALTER TABLE messages ADD COLUMN channel TEXT DEFAULT 'sms'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    return conn


def open_lead(conn, phone: str, call_id: int | None, now_iso: str, state: str = "awaiting_reply") -> int:
    """Возвращает активный лид по телефону или создаёт новый."""
    row = conn.execute(
        "SELECT id FROM leads WHERE phone=? AND state NOT IN ('booked','declined') "
        "ORDER BY id DESC LIMIT 1", (phone,),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO leads(phone, call_id, created_at, state, last_activity) VALUES(?,?,?,?,?)",
        (phone, call_id, now_iso, state, now_iso),
    )
    conn.commit()
    return cur.lastrowid


def add_message(conn, lead_id: int, direction: str, text: str, at_iso: str,
                channel: str = "sms"):
    conn.execute(
        "INSERT INTO messages(lead_id, direction, text, at, channel) VALUES(?,?,?,?,?)",
        (lead_id, direction, text, at_iso, channel),
    )
    conn.execute("UPDATE leads SET last_activity=? WHERE id=?", (at_iso, lead_id))
    conn.commit()
