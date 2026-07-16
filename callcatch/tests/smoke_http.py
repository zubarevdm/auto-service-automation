# -*- coding: utf-8 -*-
"""Дымовой тест HTTP-контура: вебхуки, авторизация, дашборд, отчёт."""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8040"
TOKEN = "dev-token-change-me"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def post(path, obj, token=TOKEN):
    req = urllib.request.Request(
        f"{BASE}{path}?token={token}",
        data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as r:
        return r.status, r.read().decode("utf-8")


ok = True

code, body = post("/webhook/call", {"phone": "+7 (999) 111-22-33", "status": "missed",
                                    "at": "2026-07-09 12:30"})
print("call missed:", code, body)
ok = ok and bool(code == 200 and body.get("lead_id"))

code, body = post("/webhook/sms", {"phone": "79991112233",
                                   "text": "нужна замена масла завтра утром",
                                   "at": "2026-07-09 12:40"})
print("sms:", code, body)
ok = ok and bool(code == 200 and "масл" in body.get("reply", ""))

code, body = post("/webhook/sms", {"phone": "79991112233", "text": "да, давайте",
                                   "at": "2026-07-09 12:50"})
print("sms yes:", code, body)
ok = ok and bool(code == 200 and "Записали" in body.get("reply", ""))

code, body = post("/webhook/call", {"phone": "79991112233", "status": "missed"}, token="wrong")
print("bad token:", code, body)
ok = ok and bool(code == 403)

# Zadarma-формат
code, body = post("/webhook/call", {"event": "NOTIFY_END", "caller_id": "+79995556677",
                                    "disposition": "no answer"})
print("zadarma:", code, body)
ok = ok and bool(code == 200 and body.get("lead_id"))

code, html = get("/")
print("dashboard:", code, "длина", len(html), "содержит тайлы:", "звонков за 7 дней" in html)
ok = ok and bool(code == 200 and "звонков за 7 дней" in html)

code, html = get("/report")
print("report:", code, "длина", len(html), "воронка:", "Воронка перехвата" in html)
ok = ok and bool(code == 200 and "Воронка перехвата" in html)

# --- голосовой контур ---
code, body = post("/api/voice/start", {"phone": "79997770001", "at": "2026-07-15 11:00"})
print("voice start:", code, body.get("action"), body.get("say", "")[:60])
ok = ok and bool(code == 200 and body.get("action") == "continue" and body.get("session_id"))
sid = body["session_id"]

code, body = post("/api/voice/utterance",
                  {"session_id": sid, "text": "нужен шиномонтаж завтра утром",
                   "elapsed_sec": 20, "at": "2026-07-15 11:01"})
print("voice utt:", code, body.get("say", "")[:70])
ok = ok and bool(code == 200 and "шиномонтаж" in body.get("say", ""))

code, body = post("/api/voice/utterance",
                  {"session_id": sid, "text": "да", "elapsed_sec": 40,
                   "at": "2026-07-15 11:02"})
print("voice yes:", code, body.get("action"), body.get("say", "")[:70])
ok = ok and bool(code == 200 and body.get("action") == "hangup")

code, body = post("/api/voice/end", {"session_id": sid, "duration_sec": 55,
                                     "at": "2026-07-15 11:02"})
print("voice end:", code, body)
ok = ok and bool(code == 200 and body.get("outcome") == "booked")

print("SMOKE:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
