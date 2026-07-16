# -*- coding: utf-8 -*-
"""Диалоговый движок: ведёт SMS-переписку от пропущенного звонка до записи.
Конечный автомат + правила NLU; llm_reply() — точка подключения GigaChat/YandexGPT."""
from datetime import datetime, timedelta

from . import booking, config as cfg, db, nlu
from .notify import notify_admin

ISO = "%Y-%m-%d %H:%M"


def service_title(key: str | None) -> str:
    return cfg.SERVICES.get(key or "generic", cfg.SERVICES["generic"])[0]


def service_acc(key: str | None) -> str:
    """Винительный падеж: «записали на диагностику»."""
    return cfg.SERVICE_ACC.get(key or "generic", cfg.SERVICE_ACC["generic"])


# ---------- исходящие тексты ----------

def first_sms(after_hours: bool, now: datetime) -> str:
    if after_hours:
        return (f"{cfg.SERVICE_NAME}: видим ваш звонок — мы уже закрылись, простите! "
                "Напишите в ответ, что нужно и когда удобно, — запишем вас без очереди.")
    return (f"{cfg.SERVICE_NAME}: не смогли ответить на ваш звонок, простите! "
            "Напишите в ответ, что нужно машине и когда удобно подъехать, — запишем без очереди.")


def offer_text(slots, service_key, now) -> str:
    labels = [booking.slot_label(s, now) for s in slots]
    svc = service_acc(service_key)
    if len(labels) >= 2:
        return f"Можем принять на {svc} {labels[0]} или {labels[1]}. Как удобнее?"
    return f"Ближайшее свободное время на {svc} — {labels[0]}. Подходит?"


def booked_text(slot, service_key, now) -> str:
    return (f"Записали вас на {service_acc(service_key)} {booking.slot_label(slot, now)}. "
            f"Адрес: {cfg.SERVICE_ADDRESS}. Если планы изменятся — просто напишите сюда.")


HANDOFF_TEXT = ("Передал ваш вопрос администратору — он перезвонит в рабочее время. "
                f"Или позвоните нам ещё раз: {cfg.SERVICE_PHONE}.")
DECLINED_TEXT = "Понял вас, хорошего дня! Будем рады помочь, когда понадобится."
STOP_TEXT = "Больше не побеспокоим. Хорошего дня!"
ASK_SERVICE_TEXT = "Подскажите, что нужно машине — шиномонтаж, масло, диагностика, ремонт?"


def llm_reply(history: list[dict], parsed: dict) -> str | None:
    """Точка подключения LLM (GigaChat / YandexGPT): вернуть текст ответа или None,
    чтобы сработали правила. Включается config.USE_LLM."""
    return None


# ---------- обработка событий ----------

def on_missed_call(conn, sms, phone: str, now: datetime, after_hours: bool,
                   call_id: int | None = None) -> int:
    """Пропущенный звонок → лид + первое SMS (с предохранителями)."""
    if phone in cfg.BLACKLIST:
        return 0
    week_ago = (now - timedelta(days=7)).strftime(ISO)
    chains = conn.execute(
        "SELECT COUNT(*) c FROM leads WHERE phone=? AND created_at>?", (phone, week_ago),
    ).fetchone()["c"]
    if chains >= cfg.MAX_CHAINS_PER_PHONE_WEEK:   # зачастил — без новых цепочек
        return 0
    now_iso = now.strftime(ISO)
    lead_id = db.open_lead(conn, phone, call_id, now_iso)
    already = conn.execute(
        "SELECT COUNT(*) c FROM messages WHERE lead_id=? AND direction='out'", (lead_id,),
    ).fetchone()["c"]
    if already:      # повторный недозвон того же клиента — не спамим
        return lead_id
    text = first_sms(after_hours, now)
    sms.send(phone, text)
    db.add_message(conn, lead_id, "out", text, now_iso)
    return lead_id


def _propose(conn, lead, time_pref, now):
    """Ищет слоты под пожелание, формирует ответ и новое состояние."""
    kw = dict(date=None, hour_from=None, hour_to=None)
    if time_pref:
        kw = dict(date=time_pref["date"], hour_from=time_pref["hour_from"],
                  hour_to=time_pref["hour_to"])
    slots = booking.find_slots(conn, now, **kw)
    if not slots:
        return None, HANDOFF_TEXT, "handoff"
    return slots[0], offer_text(slots, lead["service"], now), "confirm"


def on_inbound_sms(conn, sms, phone: str, text: str, now: datetime) -> str:
    """Входящее SMS клиента. Возвращает текст ответа бота (для журнала/тестов)."""
    if phone in cfg.BLACKLIST:
        return ""
    now_iso = now.strftime(ISO)
    lead_id = db.open_lead(conn, phone, None, now_iso)
    db.add_message(conn, lead_id, "in", text, now_iso)
    # предохранитель: не больше N исходящих одному клиенту в сутки
    day_ago = (now - timedelta(days=1)).strftime(ISO)
    out_today = conn.execute(
        "SELECT COUNT(*) c FROM messages m JOIN leads l ON l.id=m.lead_id "
        "WHERE l.phone=? AND m.direction='out' AND m.at>?", (phone, day_ago),
    ).fetchone()["c"]
    if out_today >= cfg.MAX_OUT_PER_LEAD_PER_DAY:
        return ""       # входящие бесплатны — молчим, деньги не жжём
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()

    parsed = nlu.parse(text, now)
    reply, new_state, slot_iso = None, lead["state"], lead["slot_proposed"]
    service = parsed["service"] or lead["service"]

    if cfg.USE_LLM:
        history = [dict(r) for r in conn.execute(
            "SELECT direction, text FROM messages WHERE lead_id=? ORDER BY id", (lead_id,))]
        reply = llm_reply(history, parsed)

    if reply is None:
        if parsed["intent"] == "stop":
            reply, new_state = STOP_TEXT, "declined"
        elif parsed["intent"] == "decline":
            reply, new_state = DECLINED_TEXT, "declined"
        elif (lead["state"] == "confirm" and parsed["intent"] == "yes"
              and lead["slot_proposed"] and not parsed["time"]):
            # «да» без нового времени — бронируем предложенный слот;
            # «давайте завтра вечером» уйдёт ниже в подбор нового слота
            slot = datetime.strptime(lead["slot_proposed"], ISO)
            booking.create(conn, lead_id, phone, service or "generic", slot, now)
            reply, new_state = booked_text(slot, service, now), "booked"
            notify_admin(conn, now, f"Новая запись: {phone}, {service_title(service)}, "
                                    f"{booking.slot_label(slot, now)} (через Перехват)")
        elif parsed["time"] or (lead["state"] == "confirm" and parsed["intent"] != "yes"):
            slot, reply, new_state = _propose(conn, lead if service == lead["service"] else
                                              {**dict(lead), "service": service},
                                              parsed["time"], now)
            slot_iso = slot.strftime(ISO) if slot else None
            if new_state == "handoff":
                notify_admin(conn, now, f"Перехват не подобрал слот для {phone} — перезвоните.")
        elif service and not parsed["time"]:
            slot, reply, new_state = _propose(conn, {**dict(lead), "service": service}, None, now)
            slot_iso = slot.strftime(ISO) if slot else None
        else:
            fails = lead["fail_count"] + 1
            conn.execute("UPDATE leads SET fail_count=? WHERE id=?", (fails, lead_id))
            if fails > cfg.MAX_MISUNDERSTANDINGS:
                reply, new_state = HANDOFF_TEXT, "handoff"
                notify_admin(conn, now, f"Перехват не понял клиента {phone} — перезвоните. "
                                        f"Последнее сообщение: «{text[:80]}»")
            else:
                reply, new_state = ASK_SERVICE_TEXT, "ask_service"

    conn.execute(
        "UPDATE leads SET state=?, service=?, slot_proposed=? WHERE id=?",
        (new_state, service, slot_iso, lead_id),
    )
    conn.commit()
    sms.send(phone, reply)
    db.add_message(conn, lead_id, "out", reply, now_iso)
    return reply


def on_answered_callback(conn, phone: str, now: datetime):
    """Клиент перезвонил и дозвонился сам — лид закрывается, бот замолкает."""
    conn.execute(
        "UPDATE leads SET state='declined' WHERE phone=? AND state IN "
        "('awaiting_reply','ask_service','ask_time')", (phone,),
    )
    conn.commit()
