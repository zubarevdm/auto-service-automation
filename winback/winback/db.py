# -*- coding: utf-8 -*-
"""SQLite-хранилище. Одна база = один автосервис."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY,
    name TEXT,
    phone TEXT UNIQUE NOT NULL,
    consent INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cars (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    brand TEXT, model TEXT, year INTEGER,
    UNIQUE(client_id, brand, model)
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    car_id INTEGER REFERENCES cars(id),
    visit_date TEXT NOT NULL,           -- ISO YYYY-MM-DD
    amount REAL DEFAULT 0,
    mileage INTEGER,
    services TEXT,
    UNIQUE(client_id, visit_date, amount)
);
CREATE TABLE IF NOT EXISTS touches (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    segment TEXT NOT NULL,
    step INTEGER NOT NULL,
    scheduled_date TEXT NOT NULL,       -- ISO
    text TEXT,
    status TEXT DEFAULT 'planned'       -- planned | sent | stopped
);
CREATE TABLE IF NOT EXISTS attributions (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    touch_id INTEGER NOT NULL REFERENCES touches(id),
    visit_id INTEGER NOT NULL UNIQUE REFERENCES visits(id),
    amount REAL NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_client(conn, name: str, phone: str) -> int:
    row = conn.execute("SELECT id, name FROM clients WHERE phone=?", (phone,)).fetchone()
    if row:
        if name and not row["name"]:
            conn.execute("UPDATE clients SET name=? WHERE id=?", (name, row["id"]))
        return row["id"]
    cur = conn.execute("INSERT INTO clients(name, phone) VALUES(?,?)", (name, phone))
    return cur.lastrowid


def upsert_car(conn, client_id: int, brand: str, model: str, year) -> int | None:
    if not brand and not model:
        return None
    row = conn.execute(
        "SELECT id FROM cars WHERE client_id=? AND IFNULL(brand,'')=? AND IFNULL(model,'')=?",
        (client_id, brand or "", model or ""),
    ).fetchone()
    if row:
        if year:
            conn.execute("UPDATE cars SET year=IFNULL(year,?) WHERE id=?", (year, row["id"]))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO cars(client_id, brand, model, year) VALUES(?,?,?,?)",
        (client_id, brand or "", model or "", year),
    )
    return cur.lastrowid


def insert_visit(conn, client_id, car_id, visit_date, amount, mileage, services) -> bool:
    """True — новый визит, False — дубль (уже был в базе)."""
    try:
        conn.execute(
            "INSERT INTO visits(client_id, car_id, visit_date, amount, mileage, services) "
            "VALUES(?,?,?,?,?,?)",
            (client_id, car_id, visit_date, amount, mileage, services),
        )
        return True
    except sqlite3.IntegrityError:
        return False
