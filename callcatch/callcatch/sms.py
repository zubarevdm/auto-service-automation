# -*- coding: utf-8 -*-
"""Отправка SMS. В разработке/демо — ConsoleSms (пишет в лог-файл),
в бою — SmsRu (реализовано, нужен api_id с sms.ru и согласованный отправитель)."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

from . import config as cfg


class ConsoleSms:
    """Заглушка: SMS не уходят, а складываются в outbox-файл — удобно смотреть демо."""

    def __init__(self, outbox: Path | None = None):
        self.outbox = outbox
        self.sent = []

    def send(self, phone: str, text: str) -> bool:
        self.sent.append((phone, text))
        if self.outbox:
            with open(self.outbox, "a", encoding="utf-8") as f:
                f.write(f"--> {phone}: {text}\n")
        return True


class SmsRu:
    """Боевой провайдер sms.ru. Приём ВХОДЯЩИХ ответов настраивается
    в кабинете sms.ru (callback на /webhook/sms) — см. README."""

    def __init__(self, api_id: str = "", sender: str = ""):
        self.api_id = api_id or cfg.SMSRU_API_ID
        self.sender = sender or cfg.SMS_SENDER
        if not self.api_id:
            raise RuntimeError("Нет CC_SMSRU_API_ID — задайте ключ или используйте console-провайдер")

    def send(self, phone: str, text: str) -> bool:
        params = {"api_id": self.api_id, "to": phone, "msg": text, "json": 1}
        if self.sender:
            params["from"] = self.sender
        url = "https://sms.ru/sms/send?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
        return data.get("status") == "OK"


class ExolveSms:
    """МТС Exolve Messaging API (данные в РФ, один поставщик с телефонией).
    POST https://api.exolve.ru/messaging/v1/SendSMS, Bearer-авторизация."""

    def __init__(self, api_key: str = "", number: str = ""):
        self.api_key = api_key or cfg.EXOLVE_API_KEY
        self.number = number or cfg.EXOLVE_NUMBER
        if not self.api_key or not self.number:
            raise RuntimeError("Нужны CC_EXOLVE_API_KEY и CC_EXOLVE_NUMBER (ЛК Exolve)")

    def send(self, phone: str, text: str) -> bool:
        req = urllib.request.Request(
            "https://api.exolve.ru/messaging/v1/SendSMS",
            data=json.dumps({"number": self.number, "destination": phone,
                             "text": text}).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return "message_id" in json.loads(r.read().decode())


def get_provider(outbox: Path | None = None):
    if cfg.SMS_PROVIDER == "smsru":
        return SmsRu()
    if cfg.SMS_PROVIDER == "exolve":
        return ExolveSms()
    return ConsoleSms(outbox)
