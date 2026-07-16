# -*- coding: utf-8 -*-
"""Демо-режим: три недели жизни автосервиса — поток звонков, пропуски,
SMS-диалоги (через настоящий движок), записи. Для показа владельцу."""
import random
from datetime import datetime, timedelta

from . import config as cfg, events

# Сценарии клиентов: последовательность их ответов боту.
# Движок отвечает по-настоящему — здесь только реплики клиента.
PERSONAS = [
    (["Здравствуйте, хотел записаться на замену масла завтра", "Да, давайте"], 30),
    (["Нужен шиномонтаж", "Можно в субботу утром?", "Подходит"], 25),
    (["Сколько стоит диагностика подвески? Стук какой-то", "давайте завтра вечером", "ок"], 20),
    (["Машина не заводится, гляньте", "сегодня после 16", "да"], 10),
    (["Тормоза скрипят", "в пятницу днём", "хорошо"], 15),
    (["Не надо, уже сделал в другом месте"], 25),
    (["Кто это?", "А, понял. Не надо"], 15),
    (["Уберите мой номер"], 5),
    ([], 200),  # большинство молчит — реалистичный отклик на SMS ~40%
]


def run(conn, sms, days: int = 21, seed: int = 42, end: datetime | None = None) -> dict:
    rng = random.Random(seed)
    end = end or datetime.now().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    phone_seq = 1000

    day = start
    while day < end:
        wd = day.weekday()
        hours = cfg.WORKING_HOURS.get(wd)
        n_calls = rng.randint(28, 55) if hours else rng.randint(4, 10)
        for _ in range(n_calls):
            phone_seq += 1
            phone = f"79{rng.randint(10, 99)}555{phone_seq % 10000:04d}"
            if hours:
                # пики: утро и вечер — там же и пропуски чаще
                hour = rng.choices(range(8, 21),
                                   weights=[2, 6, 8, 6, 4, 3, 3, 3, 4, 6, 8, 6, 2])[0]
            else:
                hour = rng.choice([8, 9, 19, 20, 21])
            at = day.replace(hour=min(hour, 23)) + timedelta(minutes=rng.randint(0, 59))
            in_hours = hours and hours[0] <= at.hour < hours[1]
            answered = in_hours and rng.random() > (0.38 if at.hour in (9, 10, 18, 19) else 0.22)

            res = events.handle_call(conn, sms, phone, at, answered, source="sim")
            if res["lead_id"]:
                script = rng.choices([p for p, _ in PERSONAS],
                                     weights=[w for _, w in PERSONAS])[0]
                t = at
                for msg in script:
                    t += timedelta(minutes=rng.randint(3, 90))
                    events.handle_sms(conn, sms, phone, msg, t)
        day += timedelta(days=1)

    # часть записавшихся «приехала» (для отчёта со статусами)
    for b in conn.execute("SELECT id, slot FROM bookings").fetchall():
        if datetime.strptime(b["slot"], "%Y-%m-%d %H:%M") < end - timedelta(days=1):
            status = "visited" if rng.random() < 0.85 else "no_show"
            conn.execute("UPDATE bookings SET status=? WHERE id=?", (status, b["id"]))
    conn.commit()

    # тайный аудит для продажного отчёта
    conn.execute("DELETE FROM audit_calls")
    for (day_back, hour, minute), result in [
        ((2, 10, 10), "answered"),
        ((2, 12, 40), "no_answer"),
        ((2, 18, 20), "no_answer"),
        ((1, 11, 30), "answered"),
        ((1, 17, 45), "busy"),
        ((1, 19, 5), "no_answer"),
    ]:
        at = (end - timedelta(days=day_back)).replace(hour=hour, minute=minute)
        conn.execute("INSERT INTO audit_calls(target, at, result) VALUES(?,?,?)",
                     ("Демо-СТО «Гараж»", at.strftime("%Y-%m-%d %H:%M"), result))
    conn.commit()
    return {"start": start, "end": end}
