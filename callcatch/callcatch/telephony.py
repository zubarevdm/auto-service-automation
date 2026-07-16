# -*- coding: utf-8 -*-
"""Приём событий телефонии. Внедрение на стороне сервиса — одна настройка:
переадресация непринятых вызовов на арендованный номер (или подключение
вебхуков АТС). Поддержаны форматы Zadarma и Novofon; generic — для теста руками."""
from datetime import datetime

from . import config as cfg
from .normalize import norm_phone

ISO = "%Y-%m-%d %H:%M"


def is_working_time(dt: datetime) -> bool:
    hours = cfg.WORKING_HOURS.get(dt.weekday())
    return hours is not None and hours[0] <= dt.hour < hours[1]


def parse_event(payload: dict) -> dict | None:
    """Приводит вебхук любого поддержанного провайдера к общему виду:
    {'phone', 'at': datetime, 'answered': bool}. None — событие не о звонке."""
    # generic: {"phone": "...", "status": "missed"|"answered", "at": "YYYY-MM-DD HH:MM"}
    if "phone" in payload and "status" in payload:
        phone = norm_phone(payload["phone"])
        if not phone:
            return None
        at = (datetime.strptime(payload["at"], ISO) if payload.get("at") else datetime.now())
        return {"phone": phone, "at": at, "answered": payload["status"] == "answered"}

    # Zadarma NOTIFY_END: caller_id, disposition ('answered'|'busy'|'cancel'|'no answer'...)
    if payload.get("event") in ("NOTIFY_END", "NOTIFY_OUT_END") or "disposition" in payload:
        phone = norm_phone(payload.get("caller_id") or payload.get("destination"))
        if not phone:
            return None
        return {"phone": phone, "at": datetime.now(),
                "answered": payload.get("disposition") == "answered"}

    # Novofon (совместим с Zadarma API) / Mango-подобный: call_state + numbers
    if "call_state" in payload:
        phone = norm_phone(payload.get("from") or payload.get("caller"))
        if not phone:
            return None
        return {"phone": phone, "at": datetime.now(),
                "answered": payload.get("call_state") == "answered"}

    return None
