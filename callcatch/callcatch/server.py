# -*- coding: utf-8 -*-
"""HTTP-сервер: вебхуки телефонии/SMS + дашборд. Стандартная библиотека,
никаких зависимостей — работает где угодно (Beget VPS, локальная машина).

Вебхуки (POST, JSON, ?token=WEBHOOK_TOKEN):
  /webhook/call  — событие звонка (generic/Zadarma/Novofon, см. telephony.py)
  /webhook/sms   — входящее SMS: {"phone": "...", "text": "..."}
Дашборд (GET): /  /lead?id=N  /report  /health
"""
import json
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as cfg, db, events, reports, telephony, voice
from .normalize import norm_phone
from .sms import get_provider

STATE_RU = {"awaiting_reply": "ждём ответа", "ask_service": "уточняем", "ask_time": "уточняем",
            "confirm": "подтверждает", "booked": "записан", "declined": "закрыт",
            "handoff": "у администратора"}
STATE_COLOR = {"booked": "good", "handoff": "bad"}


def dashboard_html(conn) -> str:
    now = datetime.now()
    s = reports.stats(conn, now - timedelta(days=7), now)
    tiles = f"""
    <div class='tiles'>
      <div class='tile'><div class='v'>{s['total']}</div><div class='l'>звонков за 7 дней</div></div>
      <div class='tile'><div class='v bad'>{s['lost']}</div><div class='l'>пропущено</div></div>
      <div class='tile'><div class='v'>{s['booked']}</div><div class='l'>записей через Перехват</div></div>
      <div class='tile'><div class='v good'>{reports.money(s['revenue'])}</div>
        <div class='l'>спасённая выручка</div></div>
    </div>"""
    rows = conn.execute("""
        SELECT l.id, l.phone, l.state, l.service, l.last_activity,
               (SELECT text FROM messages WHERE lead_id=l.id ORDER BY id DESC LIMIT 1) last_msg
        FROM leads l ORDER BY l.last_activity DESC LIMIT 40""").fetchall()
    trs = "".join(
        f"<tr><td><a href='/lead?id={r['id']}'>+{r['phone']}</a></td>"
        f"<td class='{STATE_COLOR.get(r['state'], '')}'>{STATE_RU.get(r['state'], r['state'])}</td>"
        f"<td>{r['last_activity'] or ''}</td>"
        f"<td>{(r['last_msg'] or '')[:70]}</td></tr>" for r in rows)
    body = tiles + (f"<div class='card'><h2>Диалоги</h2><table>"
                    f"<tr><th>Клиент</th><th>Статус</th><th>Активность</th><th>Последнее сообщение</th></tr>"
                    f"{trs}</table><p class='note'><a href='/report'>Отчёт владельцу за неделю →</a></p></div>")
    return reports.page("Перехват — дашборд", cfg.SERVICE_NAME, body)


def lead_html(conn, lead_id: int) -> str:
    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        return reports.page("Не найдено", "", "<p>Нет такого диалога.</p>")
    msgs = conn.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY id", (lead_id,)).fetchall()
    chat = "".join(
        f"<div style='margin:8px 0;{'text-align:right' if m['direction'] == 'in' else ''}'>"
        f"<span style='display:inline-block;max-width:56ch;padding:10px 14px;border-radius:12px;"
        f"background:{'var(--bar)' if m['direction'] == 'in' else 'var(--grid)'};"
        f"color:{'#fff' if m['direction'] == 'in' else 'var(--ink)'}'>{m['text']}</span>"
        f"<div class='note' style='margin-top:2px'>{m['at']} · "
        f"{'клиент' if m['direction'] == 'in' else 'бот'}</div></div>"
        for m in msgs)
    body = (f"<div class='card'><h2>+{lead['phone']} · {STATE_RU.get(lead['state'], lead['state'])}"
            f"</h2>{chat}<p class='note'><a href='/'>← к списку</a></p></div>")
    return reports.page("Диалог", cfg.SERVICE_NAME, body)


def make_handler(conn, sms):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, content: str, ctype="text/html; charset=utf-8"):
            data = content.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def log_message(self, fmt, *args):  # тихий лог
            pass

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(url.query)
            if url.path == "/health":
                return self._json(200, {"ok": True})
            if url.path == "/":
                return self._send(200, dashboard_html(conn))
            if url.path == "/lead":
                return self._send(200, lead_html(conn, int(qs.get("id", ["0"])[0])))
            if url.path == "/report":
                now = datetime.now()
                out = Path(cfg.__file__).resolve().parent.parent / "output" / "отчёт_недели.html"
                out.parent.mkdir(exist_ok=True)
                reports.owner_report(conn, now - timedelta(days=7), now, out)
                return self._send(200, out.read_text(encoding="utf-8"))
            self._send(404, "<h1>404</h1>")

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(url.query)
            if qs.get("token", [""])[0] != cfg.WEBHOOK_TOKEN:
                return self._json(403, {"error": "bad token"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw) if raw.strip().startswith("{") else \
                    {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "bad payload"})

            if url.path == "/webhook/call":
                ev = telephony.parse_event(payload)
                if not ev:
                    return self._json(400, {"error": "unrecognized event"})
                res = events.handle_call(conn, sms, ev["phone"], ev["at"], ev["answered"])
                return self._json(200, res)

            # --- голосовой ассистент (вызывается сценарием телефонии) ---
            if url.path == "/api/voice/start":
                phone = norm_phone(payload.get("phone"))
                if not phone:
                    return self._json(400, {"error": "need phone"})
                at = (datetime.strptime(payload["at"], "%Y-%m-%d %H:%M")
                      if payload.get("at") else datetime.now())
                res = voice.start(conn, phone, at)
                if res["action"] == "reject":
                    # голос недоступен (бюджет/стоп-лист) — клиента догоняет SMS
                    events.handle_call(conn, sms, phone, at, answered=False)
                return self._json(200, res)

            if url.path == "/api/voice/utterance":
                at = (datetime.strptime(payload["at"], "%Y-%m-%d %H:%M")
                      if payload.get("at") else datetime.now())
                res = voice.utterance(conn, sms, int(payload.get("session_id", 0)),
                                      payload.get("text", ""), at,
                                      int(payload.get("elapsed_sec", 0)))
                return self._json(200, res)

            if url.path == "/api/voice/end":
                at = (datetime.strptime(payload["at"], "%Y-%m-%d %H:%M")
                      if payload.get("at") else datetime.now())
                res = voice.end(conn, int(payload.get("session_id", 0)),
                                int(payload.get("duration_sec", 0)), at)
                return self._json(200, res)

            if url.path == "/webhook/sms":
                phone = norm_phone(payload.get("phone") or payload.get("from"))
                text = payload.get("text") or payload.get("message") or ""
                if not phone or not text:
                    return self._json(400, {"error": "need phone and text"})
                at = (datetime.strptime(payload["at"], "%Y-%m-%d %H:%M")
                      if payload.get("at") else datetime.now())
                reply = events.handle_sms(conn, sms, phone, text, at)
                return self._json(200, {"reply": reply})

            self._json(404, {"error": "unknown path"})

    return Handler


def serve(db_path: Path, port: int | None = None):
    conn = db.connect(db_path)
    outbox = db_path.parent / "sms_outbox.txt"
    sms = get_provider(outbox)
    port = port or cfg.HTTP_PORT
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(conn, sms))
    print(f"Перехват запущен: http://localhost:{port}  (webhook token: {cfg.WEBHOOK_TOKEN})")
    if cfg.SMS_PROVIDER == "console":
        print(f"SMS-провайдер: console (исходящие пишутся в {outbox})")
    httpd.serve_forever()
