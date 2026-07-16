# -*- coding: utf-8 -*-
"""Свободные слоты и журнал записей. Журнал — у нас, не у сервиса:
это первоисточник атрибуции и защита от «клиент не приезжал»."""
from datetime import datetime, timedelta

from . import config as cfg

RU_DAYS = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
RU_DAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def slot_label(slot: datetime, now: datetime) -> str:
    """«завтра в 10:00», «в субботу в 12:00» — как сказал бы человек."""
    d = (slot.date() - now.date()).days
    if d == 0:
        day = "сегодня"
    elif d == 1:
        day = "завтра"
    else:
        day = f"в {RU_DAYS[slot.weekday()]}"
    return f"{day} в {slot:%H:%M}"


def _hour_taken(conn, slot: datetime) -> bool:
    n = conn.execute(
        "SELECT COUNT(*) c FROM bookings WHERE slot=? AND status!='no_show'",
        (slot.strftime("%Y-%m-%d %H:00"),),
    ).fetchone()["c"]
    return n >= cfg.SLOT_CAPACITY_PER_HOUR


def find_slots(conn, now: datetime, date=None, hour_from=None, hour_to=None,
               limit: int = cfg.SLOTS_TO_OFFER) -> list[datetime]:
    """Ближайшие свободные слоты, учитывая график, занятость и пожелание клиента.
    Если пожелание невыполнимо — ближайшие доступные (бот скажет «а можем вот когда»)."""
    earliest = now + timedelta(hours=cfg.MIN_LEAD_HOURS)
    result = []
    for day_shift in range(0, 14):
        day = (date or earliest.date()) + timedelta(days=day_shift)
        hours = cfg.WORKING_HOURS.get(day.weekday())
        if hours is None:
            continue
        open_h, close_h = hours
        lo = max(open_h, hour_from if hour_from is not None else open_h)
        hi = min(close_h, hour_to if hour_to is not None else close_h)
        for h in range(lo, hi):
            slot = datetime(day.year, day.month, day.day, h)
            if slot < earliest or _hour_taken(conn, slot):
                continue
            result.append(slot)
            if len(result) >= limit:
                return result
        # пожелание по конкретной дате/часу не влезло — со второго дня ищем без ограничений
        if date and day_shift == 0 and not result:
            date = None
            hour_from = hour_to = None
    return result


def create(conn, lead_id: int, phone: str, service_key: str, slot: datetime, now: datetime) -> int:
    title, _, price = cfg.SERVICES.get(service_key, cfg.SERVICES["generic"])
    cur = conn.execute(
        "INSERT INTO bookings(lead_id, phone, service, slot, price_est, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (lead_id, phone, title, slot.strftime("%Y-%m-%d %H:00"), price,
         now.strftime("%Y-%m-%d %H:%M")),
    )
    conn.commit()
    return cur.lastrowid
