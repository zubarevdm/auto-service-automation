# -*- coding: utf-8 -*-
"""Отладочный просмотр диалогов демо-базы."""
import sqlite3
import sys
from pathlib import Path

db_path = Path(__file__).resolve().parent.parent / "data" / "demo.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ids = [r["id"] for r in conn.execute(
    "SELECT DISTINCT lead_id id FROM messages WHERE direction='in' LIMIT 6")]
for lid in ids:
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    print(f"=== lead {lid} state={lead['state']} service={lead['service']}")
    for m in conn.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY id", (lid,)):
        who = "КЛИЕНТ" if m["direction"] == "in" else "бот   "
        print(f"  {m['at']} {who}: {m['text'][:110]}")
