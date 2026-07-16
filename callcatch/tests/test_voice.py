# -*- coding: utf-8 -*-
"""Тесты голосового ассистента: запись, «дайте человека», лимиты, бюджет."""
import unittest
from datetime import datetime, timedelta

from callcatch import config as cfg, db, voice
from callcatch.sms import ConsoleSms

NOW = datetime(2026, 7, 15, 11, 0)  # среда, рабочее время


def fresh():
    return db.connect(":memory:"), ConsoleSms()


def talk(conn, sms, phone, lines, start=NOW):
    """Прогоняет разговор, возвращает (session_id, последний ответ, все реплики бота)."""
    res = voice.start(conn, phone, start)
    says = [res["say"]]
    last = res
    sec = 0
    for text in lines:
        if last["action"] != "continue":
            break
        sec += 20
        last = voice.utterance(conn, sms, res["session_id"], text,
                               start + timedelta(seconds=sec), elapsed_sec=sec)
        says.append(last["say"])
    return res["session_id"], last, says


class TestVoiceFlow(unittest.TestCase):
    def test_booking_flow(self):
        conn, sms = fresh()
        sid, last, says = talk(conn, sms, "79995551001",
                               ["стучит что-то спереди", "завтра после 16", "да"])
        self.assertEqual(last["action"], "hangup")
        self.assertIn("записала", says[-1].lower())
        bk = conn.execute("SELECT * FROM bookings").fetchall()
        self.assertEqual(len(bk), 1)
        self.assertEqual(bk[0]["service"], "диагностика")
        self.assertEqual(len(sms.sent), 1)                 # SMS-подтверждение
        self.assertIn("записаны", sms.sent[0][1])
        fin = voice.end(conn, sid, 75, NOW + timedelta(seconds=75))
        self.assertEqual(fin["outcome"], "booked")
        self.assertGreater(fin["cost_est"], 0)

    def test_greeting_mentions_recording(self):
        conn, _ = fresh()
        res = voice.start(conn, "79995551002", NOW)
        self.assertIn("записывается", res["say"])

    def test_human_two_steps(self):
        conn, sms = fresh()
        _, last, says = talk(conn, sms, "79995551003",
                             ["позовите мастера", "хочу поговорить с человеком"])
        self.assertIn("занят ремонтом", says[1])            # 1-я ступень: удержание
        self.assertIn("перезвонит", says[2])                # 2-я: передаём мастеру
        self.assertEqual(last["action"], "hangup")
        notes = conn.execute("SELECT text FROM admin_notes").fetchall()
        self.assertTrue(any("просит живого мастера" in n["text"] for n in notes))

    def test_hard_cap_by_time(self):
        conn, sms = fresh()
        res = voice.start(conn, "79995551004", NOW)
        last = voice.utterance(conn, sms, res["session_id"], "эээ ну это",
                               NOW + timedelta(seconds=400),
                               elapsed_sec=cfg.VOICE_HARD_CAP_SEC + 10)
        self.assertEqual(last["action"], "hangup")

    def test_troll_turn_cap(self):
        conn, sms = fresh()
        _, last, says = talk(conn, sms, "79995551005",
                             [f"ла-ла {i}" for i in range(cfg.VOICE_MAX_TURNS + 3)])
        self.assertEqual(last["action"], "hangup")

    def test_daily_call_limit(self):
        conn, sms = fresh()
        phone = "79995551006"
        for _ in range(cfg.VOICE_MAX_CALLS_PER_PHONE_DAY):
            sid, _, _ = talk(conn, sms, phone, ["не надо"])
            voice.end(conn, sid, 30, NOW)
        res = voice.start(conn, phone, NOW + timedelta(hours=1))
        self.assertEqual(res["action"], "hangup")           # третий за день — без ИИ
        self.assertIn("перезвонит", res["say"])

    def test_booked_client_bypasses_limit(self):
        conn, sms = fresh()
        phone = "79995551007"
        sid, _, _ = talk(conn, sms, phone, ["замена масла", "завтра утром", "да"])
        voice.end(conn, sid, 60, NOW)
        sid2, _, _ = talk(conn, sms, phone, ["не надо"], start=NOW + timedelta(minutes=30))
        voice.end(conn, sid2, 30, NOW + timedelta(minutes=31))
        res = voice.start(conn, phone, NOW + timedelta(hours=2))
        self.assertEqual(res["action"], "continue")          # записан — пускаем всегда
        self.assertIn("вы записаны", res["say"].lower())     # и помним про запись

    def test_budget_switch(self):
        conn, sms = fresh()
        conn.execute(
            "INSERT INTO voice_sessions(phone, started_at, cost_est, state) VALUES(?,?,?,?)",
            ("79990000000", NOW.strftime("%Y-%m-%d %H:%M"), cfg.DAILY_VOICE_BUDGET_RUB + 1,
             "done"))
        conn.commit()
        res = voice.start(conn, "79995551008", NOW)
        self.assertEqual(res["action"], "reject")            # деградация в SMS-режим

    def test_abandoned_transcript(self):
        conn, sms = fresh()
        res = voice.start(conn, "79995551009", NOW)
        voice.utterance(conn, sms, res["session_id"], "нужны тормоза",
                        NOW + timedelta(seconds=15), elapsed_sec=15)
        fin = voice.end(conn, res["session_id"], 25, NOW + timedelta(seconds=25))
        self.assertEqual(fin["outcome"], "abandoned")
        notes = conn.execute("SELECT text FROM admin_notes").fetchall()
        self.assertTrue(any("Расшифровка звонка" in n["text"] for n in notes))


if __name__ == "__main__":
    unittest.main()
