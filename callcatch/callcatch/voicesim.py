# -*- coding: utf-8 -*-
"""Симулятор голосового звонка в консоли: тот же движок, что услышит клиент
в трубке, — но текстом. Для демо и ручной проверки реплик."""
from datetime import datetime, timedelta

from . import db, voice
from .sms import ConsoleSms

SCENARIOS = {
    "запись": [
        ("Здравствуйте, у меня стучит что-то спереди слева", 12),
        ("А завтра после шести вечера можно?", 40),
        ("Да, подходит", 60),
    ],
    "человек": [
        ("Позовите мастера, пожалуйста", 10),
        ("Нет, я хочу поговорить с человеком", 25),
    ],
    "тролль": [(f"ла-ла-ла {i}", 10 + i * 8) for i in range(14)],
    "перенос": [],  # звонок клиента с активной записью — только приветствие
}


def play(name: str, phone: str = "79995551234", conn=None, verbose=True) -> dict:
    conn = conn or db.connect(":memory:")
    sms = ConsoleSms()
    now = datetime(2026, 7, 15, 11, 0)

    res = voice.start(conn, phone, now)
    out = [("Алёна", res["say"])]
    last = res
    for text, at_sec in SCENARIOS[name]:
        if last["action"] != "continue":
            break
        out.append(("Клиент", text))
        last = voice.utterance(conn, sms, res["session_id"], text,
                               now + timedelta(seconds=at_sec), elapsed_sec=at_sec)
        out.append(("Алёна", last["say"]))
    total = SCENARIOS[name][-1][1] + 15 if SCENARIOS[name] else 20
    fin = voice.end(conn, res["session_id"], total, now + timedelta(seconds=total))
    if verbose:
        for who, text in out:
            print(f"  {who}: {text}")
        print(f"  [звонок завершён: итог={fin.get('outcome')}, "
              f"~{fin.get('cost_est', 0)} ₽, SMS клиенту: {len(sms.sent)}]")
    return {"dialog": out, "final": fin, "sms": sms.sent}


def run_all():
    for name in ("запись", "человек", "тролль"):
        print(f"\n=== Сценарий: {name} ===")
        play(name, phone=f"7999555{hash(name) % 10000:04d}")
