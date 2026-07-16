# -*- coding: utf-8 -*-
"""Уведомления администратору сервиса: журнал в БД всегда;
Telegram — если задан токен бота (CC_TG_TOKEN / CC_TG_CHAT)."""
import json
import urllib.parse
import urllib.request

from . import config as cfg


def notify_admin(conn, now, text: str):
    delivered = "log"
    if cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_ADMIN_CHAT:
        try:
            url = (f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage?"
                   + urllib.parse.urlencode({"chat_id": cfg.TELEGRAM_ADMIN_CHAT, "text": text}))
            with urllib.request.urlopen(url, timeout=10) as r:
                if json.loads(r.read().decode()).get("ok"):
                    delivered = "telegram"
        except Exception:
            pass  # телеграм лёг — запись в журнале всё равно останется
    conn.execute(
        "INSERT INTO admin_notes(at, text, delivered) VALUES(?,?,?)",
        (now.strftime("%Y-%m-%d %H:%M"), text, delivered),
    )
    conn.commit()
