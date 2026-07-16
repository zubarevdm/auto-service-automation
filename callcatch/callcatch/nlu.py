# -*- coding: utf-8 -*-
"""Разбор русскоязычных SMS клиента: намерение, услуга, желаемое время.
Правила, не ML: детерминированно, объяснимо, работает без ключей.
Точка апгрейда до LLM — dialog.llm_reply()."""
import re
from datetime import datetime, timedelta

from . import config as cfg

STOP_WORDS = ("стоп", "не пишите", "отпишите", "удалите номер", "уберите номер", "хватит писать")
DECLINE_WORDS = ("не надо", "не нужно", "передумал", "уже записал", "уже сделал", "уже починил",
                 "неактуально", "не актуально", "ошиблись", "не звонил")
YES_WORDS = ("да", "ага", "угу", "ок", "окей", "хорошо", "подходит", "давайте", "давай",
             "записывайте", "подтверждаю", "устраивает", "годится", "отлично", "+")
HUMAN_WORDS = ("человек", "оператор", "мастер", "живой", "живым", "соедини", "позови",
               "переключи", "директор", "администратор")

WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "сред": 2, "четверг": 3,
    "пятниц": 4, "суббот": 5, "воскрес": 6,
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
}
DAYPARTS = {"утр": (9, 12), "обед": (12, 15), "днем": (12, 16), "днём": (12, 16),
            "вечер": (16, 20), "ночь": (18, 20)}


def _norm(text: str) -> str:
    return " " + re.sub(r"[^\wа-яё:+]+", " ", text.lower()).strip() + " "


def detect_intent(text: str) -> str:
    """stop | decline | yes | other (порядок важен: «да не надо» — это отказ)."""
    t = _norm(text)
    if any(w in t for w in STOP_WORDS):
        return "stop"
    if ("уберите" in t or "удалите" in t or "отпишите" in t) and ("номер" in t or "меня" in t):
        return "stop"
    if any(w in t for w in DECLINE_WORDS):
        return "decline"
    first = t.strip().split()
    if first and (first[0] in YES_WORDS or t.strip() in YES_WORDS):
        return "yes"
    if any(f" {w} " in t or t.strip() == w for w in YES_WORDS):
        return "yes"
    return "other"


def detect_service(text: str) -> str | None:
    """Ключ услуги из config.SERVICES. Ключевые слова — префиксы слов
    («шин» ловит «шины», но не «машина»)."""
    t = _norm(text)
    for key, (_, keywords, _) in cfg.SERVICES.items():
        for kw in keywords:
            if re.search(r"(^|[\s,.!?])" + re.escape(kw.strip()), t):
                return key
    return None


def _next_weekday(now: datetime, wd: int) -> datetime:
    days = (wd - now.weekday()) % 7
    if days == 0 and now.hour >= 17:      # «в субботу», сказанное в субботу вечером, — про следующую
        days = 7
    return now + timedelta(days=days)


def detect_time(text: str, now: datetime) -> dict | None:
    """Возвращает {'date': date, 'hour_from': int, 'hour_to': int} или None.
    Понимает: сегодня/завтра/послезавтра, дни недели, части дня,
    «после 18», «к 15», «в 10», «10:30», выходные."""
    t = _norm(text)
    date = None
    hour_from, hour_to = None, None

    if "послезавтра" in t:
        date = (now + timedelta(days=2)).date()
    elif "завтра" in t:
        date = (now + timedelta(days=1)).date()
    elif "сегодня" in t or "сейчас" in t or "прямо щас" in t:
        date = now.date()
    elif "выходн" in t:
        date = _next_weekday(now, 5).date()
    else:
        for word, wd in WEEKDAYS.items():
            if f" {word}" in t and cfg.WORKING_HOURS.get(wd) is not None:
                date = _next_weekday(now, wd).date()
                break

    for part, (h1, h2) in DAYPARTS.items():
        if part in t:
            hour_from, hour_to = h1, h2
            break

    m = re.search(r"после\s+(\d{1,2})", t)
    if m:
        hour_from, hour_to = int(m.group(1)), 21
    else:
        m = re.search(r"(?:в|к|на)\s+(\d{1,2})(?::(\d{2}))?(?:\s|$)", t)
        if not m:
            m = re.search(r"(?<![\d.])(\d{1,2}):(\d{2})", t)
        if m:
            h = int(m.group(1))
            if 6 <= h <= 21:
                hour_from, hour_to = h, h + 1

    if date is None and hour_from is None:
        return None
    return {"date": date, "hour_from": hour_from, "hour_to": hour_to}


def wants_human(text: str) -> bool:
    """«Позовите человека / соедините с мастером» — только явные просьбы:
    слово из HUMAN_WORDS + глагол просьбы, либо короткая реплика целиком об этом."""
    t = _norm(text)
    has_word = any(re.search(r"(^|[\s,.!?])" + w, t) for w in HUMAN_WORDS)
    if not has_word:
        return False
    asks = any(v in t for v in ("соедини", "позови", "переключи", "дайте", "можно",
                                "хочу", "надо", "нужен", "поговорить", "позовите"))
    return asks or len(t.split()) <= 3


def parse(text: str, now: datetime) -> dict:
    """Полный разбор входящего сообщения."""
    return {
        "intent": detect_intent(text),
        "service": detect_service(text),
        "time": detect_time(text, now),
        "wants_human": wants_human(text),
    }
