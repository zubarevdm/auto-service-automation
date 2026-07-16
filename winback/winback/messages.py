# -*- coding: utf-8 -*-
"""Генерация персональных сообщений и плана касаний.
Фаза 0: тексты — из шаблонов (детерминированно, без LLM и без передачи ПДн наружу).
Точка подключения GigaChat/YandexGPT на Фазе 1 — функция render()."""
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from . import config as cfg

# Шаблоны: сегмент → шаг → варианты. Вариант выбирается по id клиента,
# чтобы у соседей по гаражу тексты не совпадали слово в слово.
TEMPLATES = {
    "sleeping": {
        1: ["{name}, здравствуйте! Это {svc}. Вы у нас обслуживали {car} — давно не виделись. "
            "Как машина, всё ли в порядке?",
            "{name}, добрый день! {svc} на связи. Заметили, что {car} давно к нам не заезжала. "
            "Если что-то беспокоит — подскажем бесплатно."],
        2: ["{name}, для вас как для нашего клиента — {offer}. Заодно посмотрим, "
            "не пора ли {car} на ТО. Записать вас?",
            "{name}, до конца месяца у нас {offer} для постоянных клиентов. "
            "{car} будет полезно проверить перед дальними поездками. Найти для вас время?"],
        3: ["{name}, последнее напоминание — {offer} действует ещё 3 дня. "
            "Потом запишем только в общем порядке. Бронирую время?"],
    },
    "leaving": {
        1: ["{name}, здравствуйте! {svc}. Вы были нашим постоянным клиентом — "
            "и мы вас потеряли из виду. Если что-то было не так в прошлый раз, скажите прямо, разберёмся.",
            "{name}, добрый день! Это {svc}. Соскучились по вашей {car} :) "
            "Всё ли в порядке с машиной?"],
        2: ["{name}, хотим вас вернуть: {offer}. Для {car} это хороший повод "
            "закрыть накопившееся по обслуживанию. Записать?",
            "{name}, для вас персонально — {offer}. Действует 14 дней. "
            "Подобрать удобное время для {car}?"],
        3: ["{name}, срок предложения ({offer}) истекает через 3 дня. "
            "Одно сообщение в ответ — и время за вами."],
    },
    "lost": {
        1: ["{name}, здравствуйте! {svc}. Давно не виделись — для возвращения дарим: {offer}. "
            "Без условий и мелкого шрифта. Если неактуально — просто не отвечайте, больше не побеспокоим."],
    },
    "seasonal": {
        1: ["{name}, здравствуйте! {svc}. Пора менять резину на {season} — "
            "у нас {offer}. Выбрать вам время без очереди?",
            "{name}, добрый день! Сезон переобувки близко, запись уже открыта: {offer}. "
            "Забронировать слот для {car}?"],
        2: ["{name}, свободные окна на переобувку заканчиваются. {offer} — "
            "ещё действует. Записать {car}?"],
        3: ["{name}, последние слоты на этой неделе. Потом — живая очередь. Бронирую?"],
    },
    "mileage": {
        1: ["{name}, здравствуйте! {svc}. По нашим расчётам {car} прошла уже ~{mileage} тыс. км — "
            "подходит срок замены масла. У нас это {offer}. Записать вас?",
            "{name}, добрый день! Судя по вашим визитам, пробег {car} около {mileage} тыс. км — "
            "пора на плановое ТО. {offer}. Подобрать время?"],
        2: ["{name}, напоминаем про ТО для {car}: {offer}. "
            "Затягивать с маслом дороже — двигатель не скажет спасибо. Записать?"],
        3: ["{name}, последнее напоминание про ТО. Одно сообщение — и слот ваш. "
            "После этого не будем беспокоить."],
    },
}


def render(client: dict, segment: str, step: int, today: date) -> str:
    """Собирает текст сообщения. Сюда позже встраивается LLM-генерация."""
    variants = TEMPLATES[segment].get(step) or TEMPLATES[segment][max(TEMPLATES[segment])]
    tpl = variants[client["id"] % len(variants)]
    car = client["car"] or "ваш автомобиль"
    if client.get("car_year"):
        car = f"{car} {client['car_year']} г."
    return tpl.format(
        name=client["name"] or "Здравствуйте",
        car=car,
        svc=cfg.SERVICE_NAME,
        offer=cfg.OFFERS[segment],
        season=cfg.SEASON_MONTHS.get(today.month, "сезонную"),
        mileage=(client.get("est_mileage") or 0) // 1000,
    ) + f"\n\n{cfg.SERVICE_NAME}, {cfg.SERVICE_ADDRESS}, {cfg.SERVICE_PHONE}"


def plan_touches(conn, segmented: list[dict], today: date) -> int:
    """Ставит цепочки касаний клиентам, у которых нет активной цепочки и карантина."""
    created = 0
    for s in segmented:
        # активная или недавняя цепочка — пропускаем (карантин)
        recent = conn.execute(
            "SELECT MAX(scheduled_date) d FROM touches WHERE client_id=?",
            (s["id"],),
        ).fetchone()["d"]
        if recent and (today - date.fromisoformat(recent)).days < cfg.QUARANTINE_DAYS:
            continue
        cadence = cfg.CADENCE_LOST if s["segment"] == "lost" else cfg.CADENCE
        for step, shift in cadence:
            when = today + timedelta(days=shift)
            conn.execute(
                "INSERT INTO touches(client_id, segment, step, scheduled_date, text) "
                "VALUES(?,?,?,?,?)",
                (s["id"], s["segment"], step, when.isoformat(),
                 render(s, s["segment"], step, when)),
            )
        created += 1
    conn.commit()
    return created


def stop_chain(conn, client_id: int):
    """Клиент записался/ответил/отписался — дальнейшие касания отменяются."""
    conn.execute(
        "UPDATE touches SET status='stopped' WHERE client_id=? AND status='planned'",
        (client_id,),
    )
    conn.commit()


def export_today(conn, out_path: str | Path, today: date) -> int:
    """Выгружает сообщения на сегодня (и просроченные) в Excel — рассылать руками.
    Отмечает их отправленными."""
    rows = conn.execute("""
        SELECT t.id, t.scheduled_date, t.step, t.segment, t.text,
               c.name, c.phone
        FROM touches t JOIN clients c ON c.id = t.client_id
        WHERE t.status='planned' AND t.scheduled_date <= ?
        ORDER BY t.segment, t.step, c.name
    """, (today.isoformat(),)).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Отправить"
    ws.append(["Телефон", "Имя", "Сегмент", "Шаг", "WhatsApp-ссылка", "Текст сообщения"])
    for r in rows:
        from urllib.parse import quote
        wa = f"https://wa.me/{r['phone']}?text={quote(r['text'])}"
        ws.append([f"+{r['phone']}", r["name"], cfg.SEGMENT_TITLES[r["segment"]],
                   r["step"], wa, r["text"]])
    for col, width in zip("ABCDEF", (16, 14, 12, 6, 40, 90)):
        ws.column_dimensions[col].width = width
    wb.save(str(out_path))

    conn.executemany("UPDATE touches SET status='sent' WHERE id=?",
                     [(r["id"],) for r in rows])
    conn.commit()
    return len(rows)
