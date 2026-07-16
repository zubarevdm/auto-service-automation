# -*- coding: utf-8 -*-
"""Голосовой ассистент: ведёт телефонный разговор от приветствия до записи.
Мозг работает текстом (реплика → ответ) — распознавание и синтез речи делает
телефонная платформа (Voximplant-сценарий в integrations/), поэтому движок
тестируется без телефонии и переиспользует NLU/слоты/журнал SMS-версии.

Сценарий «поговорить с человеком» (двухступенчатый):
 1-я просьба → «мастер сейчас занят ремонтом, могу записать вас или ответить сама»;
 настаивает → «передам мастеру, что вы звонили, — перезвонит, как освободится»."""
from datetime import datetime, timedelta

from . import booking, config as cfg, db, nlu
from .dialog import service_acc
from .notify import notify_admin

ISO = "%Y-%m-%d %H:%M"

# ---------- реплики (короткие — это речь, не текст) ----------

def greeting(upcoming_slot=None, now=None) -> str:
    base = (f"{cfg.SERVICE_NAME}, здравствуйте! Это {cfg.VOICE_ASSISTANT_NAME}, помощница "
            "сервиса, звонок записывается. Мастер сейчас занят, но я могу записать вас "
            "на удобное время. Подскажите, что случилось с машиной?")
    if upcoming_slot:
        return (f"{cfg.SERVICE_NAME}, здравствуйте! Это {cfg.VOICE_ASSISTANT_NAME}. "
                f"Вижу, вы записаны к нам {booking.slot_label(upcoming_slot, now)}. "
                "Хотите перенести запись или подсказать что-то ещё?")
    return base

HUMAN_FIRST = ("Мастер сейчас занят ремонтом и подойти не может, простите. "
               "Я могу сама записать вас или ответить на вопрос — что подскажете?")
HUMAN_FINAL = ("Хорошо, я передам мастеру, что вы звонили, — он перезвонит, "
               "как только освободится. Спасибо за звонок, хорошего дня!")
BYE_DECLINED = "Поняла вас, хорошего дня! Будем рады помочь, когда понадобится."
ASK_SERVICE = "Подскажите, что нужно машине — шиномонтаж, масло, диагностика или ремонт?"
SOFT_WRAP = ("Чтобы не держать вас на линии: давайте я запишу вас на ближайшее время, "
             "а детали мастер уточнит на месте. Когда вам удобно подъехать?")
HARD_CAP = ("Простите, мне нужно освободить линию. Я передам мастеру, что вы звонили, — "
            "он перезвонит. Хорошего дня!")
LIMITED = ("Здравствуйте! Я передам мастеру, что вы звонили, — он перезвонит, "
           "как освободится. Спасибо!")


def _offer(slots, service_key, now) -> str:
    labels = [booking.slot_label(s, now) for s in slots]
    svc = service_acc(service_key)
    if len(labels) >= 2:
        return f"Могу записать вас на {svc} {labels[0]} или {labels[1]}. Как удобнее?"
    return f"Ближайшее свободное время на {svc} — {labels[0]}. Подходит?"


def _confirm_ask(slot, service_key, now) -> str:
    return (f"Записываю: {service_acc(service_key)}, {booking.slot_label(slot, now)}. "
            "Всё верно?")


def _booked_say(slot, service_key, now) -> str:
    return (f"Готово, записала вас на {service_acc(service_key)} "
            f"{booking.slot_label(slot, now)}. Пришлю подтверждение сообщением. "
            "Спасибо за звонок, ждём вас!")


# ---------- учёт бюджета ----------

def voice_spent_today(conn, now: datetime) -> float:
    day_ago = (now - timedelta(days=1)).strftime(ISO)
    row = conn.execute(
        "SELECT IFNULL(SUM(cost_est),0) s FROM voice_sessions WHERE started_at>?",
        (day_ago,)).fetchone()
    return row["s"]


def voice_allowed(conn, now: datetime) -> bool:
    return voice_spent_today(conn, now) < cfg.DAILY_VOICE_BUDGET_RUB


# ---------- жизненный цикл сессии ----------

def start(conn, phone: str, now: datetime) -> dict:
    """Начало звонка. action: continue | hangup | reject (не отвечать голосом —
    вызывающая сторона запускает SMS-fallback через events.handle_call)."""
    if phone in cfg.BLACKLIST:
        return {"session_id": None, "say": "", "action": "reject"}
    if not voice_allowed(conn, now):
        return {"session_id": None, "say": "", "action": "reject"}   # бюджет дня сожжён

    day_ago = (now - timedelta(days=1)).strftime(ISO)
    calls_today = conn.execute(
        "SELECT COUNT(*) c FROM voice_sessions WHERE phone=? AND started_at>?",
        (phone, day_ago)).fetchone()["c"]

    now_iso = now.strftime(ISO)
    lead_id = db.open_lead(conn, phone, None, now_iso, state="voice_active")
    cur = conn.execute(
        "INSERT INTO voice_sessions(phone, lead_id, started_at) VALUES(?,?,?)",
        (phone, lead_id, now_iso))
    sid = cur.lastrowid
    conn.commit()

    # записанный клиент — пускаем всегда (скорее всего перенос)
    upcoming = conn.execute(
        "SELECT slot FROM bookings WHERE phone=? AND slot>? AND status='new' "
        "ORDER BY slot LIMIT 1", (phone, now_iso)).fetchone()

    if calls_today >= cfg.VOICE_MAX_CALLS_PER_PHONE_DAY and not upcoming:
        _finish(conn, sid, now, outcome="limited")
        notify_admin(conn, now, f"Звонящий +{phone} обращается чаще лимита — перезвоните ему.")
        return {"session_id": sid, "say": LIMITED, "action": "hangup"}

    up_slot = datetime.strptime(upcoming["slot"], "%Y-%m-%d %H:%M") if upcoming else None
    say = greeting(up_slot, now)
    db.add_message(conn, lead_id, "out", say, now_iso, channel="voice")
    return {"session_id": sid, "say": say, "action": "continue"}


def utterance(conn, sms, session_id: int, text: str, now: datetime,
              elapsed_sec: int = 0) -> dict:
    """Реплика клиента → ответ. action: continue | hangup."""
    s = conn.execute("SELECT * FROM voice_sessions WHERE id=?", (session_id,)).fetchone()
    if not s or s["state"] != "active":
        return {"say": "", "action": "hangup"}
    lead_id, phone = s["lead_id"], s["phone"]
    now_iso = now.strftime(ISO)
    db.add_message(conn, lead_id, "in", text, now_iso, channel="voice")
    turns = s["turns"] + 1
    conn.execute("UPDATE voice_sessions SET turns=? WHERE id=?", (turns, session_id))

    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    parsed = nlu.parse(text, now)
    service = parsed["service"] or lead["service"]

    def reply(say, action="continue", state=None, slot_iso=None, outcome=None):
        conn.execute("UPDATE leads SET state=?, service=?, slot_proposed=? WHERE id=?",
                     (state or lead["state"], service,
                      slot_iso or lead["slot_proposed"], lead_id))
        db.add_message(conn, lead_id, "out", say, now_iso, channel="voice")
        if outcome:
            _finish(conn, session_id, now, outcome=outcome, elapsed_sec=elapsed_sec)
        conn.commit()
        return {"say": say, "action": action}

    # --- жёсткий потолок: время или количество реплик ---
    if elapsed_sec >= cfg.VOICE_HARD_CAP_SEC or turns > cfg.VOICE_MAX_TURNS:
        notify_admin(conn, now, f"Долгий звонок +{phone} завершён по лимиту — перезвоните.")
        return reply(HARD_CAP, "hangup", state="handoff", outcome="handoff")

    # --- просит человека: 1-я ступень мягкая, дальше — «мастер перезвонит» ---
    if parsed["wants_human"]:
        if not s["asked_human"]:
            conn.execute("UPDATE voice_sessions SET asked_human=1 WHERE id=?", (session_id,))
            conn.commit()
            return reply(HUMAN_FIRST)
        notify_admin(conn, now, f"Клиент +{phone} просит живого мастера — перезвоните.")
        return reply(HUMAN_FINAL, "hangup", state="handoff", outcome="handoff")

    if parsed["intent"] in ("stop", "decline"):
        return reply(BYE_DECLINED, "hangup", state="declined", outcome="declined")

    # --- подтверждение предложенного слота ---
    if lead["state"] == "confirm" and parsed["intent"] == "yes" and lead["slot_proposed"] \
            and not parsed["time"]:
        slot = datetime.strptime(lead["slot_proposed"], ISO)
        booking.create(conn, lead_id, phone, service or "generic", slot, now)
        sms.send(phone, f"{cfg.SERVICE_NAME}: вы записаны на {service_acc(service)} "
                        f"{booking.slot_label(slot, now)}. Адрес: {cfg.SERVICE_ADDRESS}. "
                        f"Если планы изменятся — позвоните: {cfg.SERVICE_PHONE}.")
        notify_admin(conn, now, f"Запись голосом: +{phone}, {service_acc(service)}, "
                                f"{booking.slot_label(slot, now)}.")
        return reply(_booked_say(slot, service, now), "hangup", state="booked", outcome="booked")

    # --- мягкое сворачивание по времени ---
    soft = elapsed_sec >= cfg.VOICE_SOFT_WRAP_SEC

    if parsed["time"] or (lead["state"] == "confirm" and parsed["intent"] != "yes"):
        kw = parsed["time"] or {}
        slots = booking.find_slots(conn, now, date=kw.get("date"),
                                   hour_from=kw.get("hour_from"), hour_to=kw.get("hour_to"),
                                   limit=1 if soft else cfg.SLOTS_TO_OFFER)
        if not slots:
            notify_admin(conn, now, f"Голос: не подобрали слот для +{phone} — перезвоните.")
            return reply(HUMAN_FINAL, "hangup", state="handoff", outcome="handoff")
        return reply(_confirm_ask(slots[0], service, now) if soft or len(slots) == 1
                     else _offer(slots, service, now),
                     state="confirm", slot_iso=slots[0].strftime(ISO))

    if service:
        slots = booking.find_slots(conn, now, limit=1 if soft else cfg.SLOTS_TO_OFFER)
        if not slots:
            notify_admin(conn, now, f"Голос: нет слотов для +{phone} — перезвоните.")
            return reply(HUMAN_FINAL, "hangup", state="handoff", outcome="handoff")
        return reply(_confirm_ask(slots[0], service, now) if soft
                     else _offer(slots, service, now),
                     state="confirm", slot_iso=slots[0].strftime(ISO))

    fails = lead["fail_count"] + 1
    conn.execute("UPDATE leads SET fail_count=? WHERE id=?", (fails, lead_id))
    if fails > cfg.MAX_MISUNDERSTANDINGS:
        notify_admin(conn, now, f"Голос: не поняла клиента +{phone} — перезвоните. "
                                f"Последняя фраза: «{text[:80]}»")
        return reply(HUMAN_FINAL, "hangup", state="handoff", outcome="handoff")
    return reply(SOFT_WRAP if soft else ASK_SERVICE)


def end(conn, session_id: int, duration_sec: int, now: datetime) -> dict:
    """Финал звонка (кладёт платформа): длительность, себестоимость, расшифровка админу."""
    s = conn.execute("SELECT * FROM voice_sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        return {}
    outcome = s["outcome"]
    if s["state"] == "active":          # клиент повесил трубку на полпути
        outcome = "abandoned" if s["turns"] else "silent"
        _finish(conn, session_id, now, outcome=outcome)
    conn.execute("UPDATE voice_sessions SET duration_sec=?, cost_est=? WHERE id=?",
                 (duration_sec, round(duration_sec * cfg.VOICE_RUB_PER_SEC, 2), session_id))
    conn.commit()
    if s["turns"]:
        msgs = conn.execute(
            "SELECT direction, text FROM messages WHERE lead_id=? AND at>=? AND channel='voice' "
            "ORDER BY id", (s["lead_id"], s["started_at"])).fetchall()
        transcript = "\n".join(
            f"{'Клиент' if m['direction'] == 'in' else cfg.VOICE_ASSISTANT_NAME}: {m['text']}"
            for m in msgs)
        notify_admin(conn, now,
                     f"Расшифровка звонка +{s['phone']} ({duration_sec} сек, итог: "
                     f"{outcome}):\n{transcript}")
    return {"outcome": outcome, "cost_est": round(duration_sec * cfg.VOICE_RUB_PER_SEC, 2)}


def _finish(conn, session_id, now, outcome, elapsed_sec: int = 0):
    conn.execute(
        "UPDATE voice_sessions SET state='done', outcome=?, ended_at=?, cost_est=? WHERE id=?",
        (outcome, now.strftime(ISO), round(elapsed_sec * cfg.VOICE_RUB_PER_SEC, 2), session_id))
    conn.commit()
