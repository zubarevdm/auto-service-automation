# -*- coding: utf-8 -*-
"""Движок сегментации. Правила объяснимы владельцу — каждое решение
сопровождается причиной («не был 8 мес при своём интервале 3 мес»)."""
from datetime import date

from . import config as cfg


def client_stats(conn, today: date) -> list[dict]:
    """Считает производные поля по каждому клиенту с согласием на связь."""
    rows = conn.execute("""
        SELECT c.id, c.name, c.phone,
               COUNT(v.id)            AS visit_count,
               MAX(v.visit_date)      AS last_visit,
               MIN(v.visit_date)      AS first_visit,
               AVG(v.amount)          AS avg_check,
               SUM(v.amount)          AS total_spent
        FROM clients c JOIN visits v ON v.client_id = c.id
        WHERE c.consent = 1
        GROUP BY c.id
    """).fetchall()

    result = []
    for r in rows:
        last = date.fromisoformat(r["last_visit"])
        first = date.fromisoformat(r["first_visit"])
        n = r["visit_count"]
        interval = (last - first).days / (n - 1) if n > 1 and last > first else None
        s = dict(r)
        s["last_visit"] = last
        s["days_since"] = (today - last).days
        s["interval"] = interval

        # Оценка пробега: две точки → личный накат, иначе средний по стране
        mrows = conn.execute(
            "SELECT visit_date, mileage FROM visits "
            "WHERE client_id=? AND mileage IS NOT NULL ORDER BY visit_date",
            (r["id"],),
        ).fetchall()
        daily = cfg.DEFAULT_DAILY_RUN_KM
        last_mileage = None
        if mrows:
            last_mileage = mrows[-1]["mileage"]
            if len(mrows) > 1:
                d0 = date.fromisoformat(mrows[0]["visit_date"])
                d1 = date.fromisoformat(mrows[-1]["visit_date"])
                km = mrows[-1]["mileage"] - mrows[0]["mileage"]
                if (d1 - d0).days > 30 and km > 0:
                    daily = km / (d1 - d0).days
        s["last_mileage"] = last_mileage
        s["run_since_visit"] = round(daily * s["days_since"])
        s["est_mileage"] = (last_mileage + s["run_since_visit"]) if last_mileage else None

        # Была ли в истории шинная услуга и когда
        s["had_tires"] = conn.execute(
            "SELECT 1 FROM visits WHERE client_id=? AND ("
            + " OR ".join("LOWER(services) LIKE ?" for _ in cfg.TIRE_KEYWORDS) + ")",
            (r["id"], *[f"%{k}%" for k in cfg.TIRE_KEYWORDS]),
        ).fetchone() is not None

        car = conn.execute(
            "SELECT brand, model, year FROM cars WHERE client_id=? ORDER BY id DESC LIMIT 1",
            (r["id"],),
        ).fetchone()
        s["car"] = f"{car['brand']} {car['model']}".strip() if car else ""
        s["car_year"] = car["year"] if car else None
        result.append(s)
    return result


def assign_segment(s: dict, today: date) -> tuple[str, str] | None:
    """Возвращает (сегмент, причина) или None (активный клиент, не трогаем)."""
    days = s["days_since"]
    iv = s["interval"]

    # Границы «спящий/уходящий» в днях: от личного интервала или fallback
    if iv and iv >= 20:
        sleep_lo, sleep_hi = iv * cfg.SLEEP_RATIO[0], iv * cfg.SLEEP_RATIO[1]
        leave_hi = iv * cfg.LEAVE_RATIO[1]
    else:
        sleep_lo, sleep_hi = cfg.SLEEP_DAYS
        leave_hi = cfg.LEAVE_DAYS[1]

    if days < sleep_lo:
        return None  # активный

    # Приоритет 1: объективный повод по пробегу
    if s["est_mileage"] and s["run_since_visit"] >= cfg.MILEAGE_TRIGGER_KM and days < cfg.LOST_DAYS:
        return "mileage", (
            f"расчётный пробег ~{s['est_mileage'] // 1000} тыс. км, "
            f"с последнего визита накатано ~{s['run_since_visit'] // 1000} тыс. км"
        )

    # Приоритет 2: сезон переобувки для тех, кто «шинился» у нас
    if today.month in cfg.SEASON_MONTHS and s["had_tires"] and days > 120:
        return "seasonal", f"сезон переобувки, последний визит {days} дн. назад"

    if days >= cfg.LOST_DAYS or (iv and iv >= 20 and days > leave_hi):
        return "lost", f"не был {days} дн. — вероятно, потерян"
    if days > sleep_hi:
        if s["visit_count"] >= cfg.MIN_VISITS_REGULAR:
            return "leaving", (
                f"был регулярным (интервал ~{round(iv)} дн.), молчит {days} дн."
                if iv else f"был регулярным, молчит {days} дн."
            )
        return "sleeping", f"не был {days} дн."
    return "sleeping", (
        f"личный интервал ~{round(iv)} дн., не был уже {days} дн." if iv
        else f"не был {days} дн."
    )


def segment_all(conn, today: date) -> list[dict]:
    """Полная сегментация базы. Возвращает клиентов с назначенным сегментом."""
    out = []
    for s in client_stats(conn, today):
        seg = assign_segment(s, today)
        if seg:
            s["segment"], s["reason"] = seg
            out.append(s)
    return out


def summary(segmented: list[dict]) -> dict:
    """Сводка по сегментам + прогноз возврата для аудита."""
    by_seg = {}
    for s in segmented:
        by_seg.setdefault(s["segment"], []).append(s)
    rows = []
    for seg in cfg.SEGMENT_ORDER:
        clients = by_seg.get(seg, [])
        if not clients:
            continue
        avg_check = sum(c["avg_check"] or 0 for c in clients) / len(clients)
        visits = len(clients) * cfg.CONVERSION[seg] * cfg.SHOW_RATE
        rows.append({
            "segment": seg, "title": cfg.SEGMENT_TITLES[seg],
            "count": len(clients), "avg_check": avg_check,
            "forecast_visits": visits, "forecast_revenue": visits * avg_check,
        })
    return {
        "rows": rows,
        "total_clients": len(segmented),
        "forecast_visits": sum(r["forecast_visits"] for r in rows),
        "forecast_revenue": sum(r["forecast_revenue"] for r in rows),
    }
