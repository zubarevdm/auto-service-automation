# -*- coding: utf-8 -*-
"""Связка: событие телефонии/SMS → база → диалоговый движок."""
from datetime import datetime

from . import dialog, telephony

ISO = "%Y-%m-%d %H:%M"


def handle_call(conn, sms, phone: str, at: datetime, answered: bool,
                source: str = "webhook") -> dict:
    """Регистрирует звонок; на непринятый — запускает перехват."""
    working = telephony.is_working_time(at)
    status = "answered" if answered else ("missed" if working else "afterhours")
    cur = conn.execute(
        "INSERT INTO calls(phone, at, status, source) VALUES(?,?,?,?)",
        (phone, at.strftime(ISO), status, source),
    )
    conn.commit()
    if answered:
        dialog.on_answered_callback(conn, phone, at)
        return {"status": status, "lead_id": None}
    lead_id = dialog.on_missed_call(conn, sms, phone, at, after_hours=not working,
                                    call_id=cur.lastrowid)
    return {"status": status, "lead_id": lead_id}


def handle_sms(conn, sms, phone: str, text: str, at: datetime) -> str:
    """Входящее SMS клиента → ответ бота."""
    return dialog.on_inbound_sms(conn, sms, phone, text, at)
