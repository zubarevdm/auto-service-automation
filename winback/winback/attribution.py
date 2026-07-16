# -*- coding: utf-8 -*-
"""Атрибуция: какие заезды привела рассылка. Ядро продаваемости —
именно эти цифры показываются владельцу и от них считается оплата."""
from datetime import date, timedelta

from . import config as cfg
from .messages import stop_chain


def match(conn) -> dict:
    """Связывает визиты с касаниями: заезд того же клиента в окне
    ATTRIBUTION_WINDOW_DAYS после отправленного касания = возвращённый."""
    touches = conn.execute(
        "SELECT id, client_id, scheduled_date FROM touches WHERE status IN ('sent','stopped')"
    ).fetchall()
    new_matches = 0
    for t in touches:
        t_date = date.fromisoformat(t["scheduled_date"])
        window_end = t_date + timedelta(days=cfg.ATTRIBUTION_WINDOW_DAYS)
        visits = conn.execute("""
            SELECT id, visit_date, amount FROM visits
            WHERE client_id=? AND visit_date > ? AND visit_date <= ?
              AND id NOT IN (SELECT visit_id FROM attributions)
        """, (t["client_id"], t_date.isoformat(), window_end.isoformat())).fetchall()
        for v in visits:
            conn.execute(
                "INSERT OR IGNORE INTO attributions(client_id, touch_id, visit_id, amount) "
                "VALUES(?,?,?,?)",
                (t["client_id"], t["id"], v["id"], v["amount"]),
            )
            new_matches += 1
            stop_chain(conn, t["client_id"])  # вернулся — цепочку глушим
    conn.commit()
    return results(conn) | {"new_matches": new_matches}


def results(conn) -> dict:
    """Итоги кампании для отчёта и счёта."""
    row = conn.execute("""
        SELECT COUNT(DISTINCT a.client_id) AS clients,
               COUNT(*)                    AS visits,
               IFNULL(SUM(a.amount), 0)    AS revenue
        FROM attributions a
    """).fetchone()
    sent = conn.execute(
        "SELECT COUNT(*) c FROM touches WHERE status IN ('sent','stopped')"
    ).fetchone()["c"]
    by_seg = conn.execute("""
        SELECT t.segment, COUNT(DISTINCT a.client_id) clients, SUM(a.amount) revenue
        FROM attributions a JOIN touches t ON t.id = a.touch_id
        GROUP BY t.segment
    """).fetchall()
    return {
        "sent": sent,
        "returned_clients": row["clients"],
        "returned_visits": row["visits"],
        "revenue": row["revenue"],
        "commission": row["revenue"] * cfg.COMMISSION,
        "by_segment": {r["segment"]: dict(r) for r in by_seg},
    }
