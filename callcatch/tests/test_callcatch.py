# -*- coding: utf-8 -*-
"""Тесты ядра: NLU, диалог до записи, слоты, отказы, эскалация."""
import unittest
from datetime import datetime

from callcatch import booking, db, dialog, events, nlu
from callcatch.sms import ConsoleSms

# среда 15 июля 2026, 11:00 — рабочее время
NOW = datetime(2026, 7, 15, 11, 0)


def fresh():
    return db.connect(":memory:"), ConsoleSms()


class TestNlu(unittest.TestCase):
    def test_yes(self):
        for t in ("Да", "да, давайте", "Ок", "подходит", "+", "Хорошо"):
            self.assertEqual(nlu.detect_intent(t), "yes", t)

    def test_decline_beats_yes(self):
        self.assertEqual(nlu.detect_intent("да не надо, уже сделал"), "decline")

    def test_stop(self):
        for t in ("СТОП", "уберите мой номер", "не пишите мне", "отпишите меня"):
            self.assertEqual(nlu.detect_intent(t), "stop", t)

    def test_service(self):
        self.assertEqual(nlu.detect_service("нужно переобуться"), "tires")
        self.assertEqual(nlu.detect_service("замена масла и фильтров"), "oil")
        self.assertEqual(nlu.detect_service("что-то стучит спереди"), "diag")
        self.assertIsNone(nlu.detect_service("привет"))

    def test_time_tomorrow_evening(self):
        t = nlu.detect_time("давайте завтра вечером", NOW)
        self.assertEqual(t["date"], datetime(2026, 7, 16).date())
        self.assertEqual((t["hour_from"], t["hour_to"]), (16, 20))

    def test_time_weekday(self):
        t = nlu.detect_time("можно в субботу утром?", NOW)
        self.assertEqual(t["date"].weekday(), 5)
        self.assertEqual(t["hour_from"], 9)

    def test_time_after(self):
        t = nlu.detect_time("сегодня после 16", NOW)
        self.assertEqual(t["date"], NOW.date())
        self.assertEqual(t["hour_from"], 16)

    def test_time_explicit(self):
        t = nlu.detect_time("запишите к 15", NOW)
        self.assertEqual((t["hour_from"], t["hour_to"]), (15, 16))

    def test_time_none(self):
        self.assertIsNone(nlu.detect_time("сколько это стоит?", NOW))


class TestBooking(unittest.TestCase):
    def test_min_lead_and_capacity(self):
        conn, _ = fresh()
        slots = booking.find_slots(conn, NOW)
        self.assertTrue(all(s >= NOW.replace(hour=13) for s in slots))
        # забиваем час полностью — слот исчезает
        for i in range(2):
            booking.create(conn, 1, f"7999000000{i}", "oil", slots[0], NOW)
        slots2 = booking.find_slots(conn, NOW)
        self.assertNotIn(slots[0], slots2)

    def test_sunday_skipped(self):
        conn, _ = fresh()
        sat_evening = datetime(2026, 7, 18, 17, 0)  # суббота, закрытие в 18
        slots = booking.find_slots(conn, sat_evening)
        self.assertTrue(all(s.weekday() != 6 for s in slots))


class TestDialog(unittest.TestCase):
    def _missed(self, conn, sms, phone="79995550001"):
        return events.handle_call(conn, sms, phone, NOW, answered=False, source="sim")

    def test_full_flow_to_booking(self):
        conn, sms = fresh()
        self._missed(conn, sms)
        self.assertEqual(len(sms.sent), 1)                     # первое SMS ушло
        events.handle_sms(conn, sms, "79995550001",
                          "Хотел записаться на замену масла завтра", NOW)
        reply = sms.sent[-1][1]
        self.assertIn("замену масла", reply)                   # предложение слота
        events.handle_sms(conn, sms, "79995550001", "Да, давайте", NOW)
        bk = conn.execute("SELECT * FROM bookings").fetchall()
        self.assertEqual(len(bk), 1)
        self.assertEqual(bk[0]["service"], "замена масла")
        lead = conn.execute("SELECT state FROM leads").fetchone()
        self.assertEqual(lead["state"], "booked")

    def test_yes_with_new_time_reproposes(self):
        conn, sms = fresh()
        self._missed(conn, sms)
        events.handle_sms(conn, sms, "79995550001", "нужна диагностика", NOW)
        events.handle_sms(conn, sms, "79995550001", "давайте завтра вечером", NOW)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"], 0)
        lead = conn.execute("SELECT * FROM leads").fetchone()
        self.assertEqual(lead["state"], "confirm")
        self.assertIn("2026-07-16", lead["slot_proposed"])     # слот на завтра
        self.assertGreaterEqual(int(lead["slot_proposed"][11:13]), 16)
        events.handle_sms(conn, sms, "79995550001", "да", NOW)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM bookings").fetchone()["c"], 1)

    def test_decline_and_stop(self):
        conn, sms = fresh()
        self._missed(conn, sms, "79995550002")
        events.handle_sms(conn, sms, "79995550002", "не надо, уже сделал", NOW)
        self.assertEqual(conn.execute("SELECT state FROM leads").fetchone()["state"], "declined")
        self._missed(conn, sms, "79995550003")
        events.handle_sms(conn, sms, "79995550003", "уберите мой номер", NOW)
        rows = conn.execute("SELECT state FROM leads ORDER BY id").fetchall()
        self.assertEqual(rows[-1]["state"], "declined")

    def test_handoff_after_misunderstandings(self):
        conn, sms = fresh()
        self._missed(conn, sms)
        for txt in ("превед", "картошка", "ааа"):
            events.handle_sms(conn, sms, "79995550001", txt, NOW)
        lead = conn.execute("SELECT state FROM leads").fetchone()
        self.assertEqual(lead["state"], "handoff")
        notes = conn.execute("SELECT COUNT(*) c FROM admin_notes").fetchone()["c"]
        self.assertGreaterEqual(notes, 1)

    def test_no_duplicate_first_sms(self):
        conn, sms = fresh()
        self._missed(conn, sms)
        self._missed(conn, sms)                                # позвонил ещё раз
        self.assertEqual(len(sms.sent), 1)

    def test_callback_answered_silences_bot(self):
        conn, sms = fresh()
        self._missed(conn, sms)
        events.handle_call(conn, sms, "79995550001", NOW, answered=True, source="sim")
        lead = conn.execute("SELECT state FROM leads").fetchone()
        self.assertEqual(lead["state"], "declined")

    def test_troll_reply_cap(self):
        conn, sms = fresh()
        self._missed(conn, sms)
        from callcatch import config as cfg
        for i in range(cfg.MAX_OUT_PER_LEAD_PER_DAY + 5):
            events.handle_sms(conn, sms, "79995550001", f"ааа {i}", NOW)
        out = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE direction='out'").fetchone()["c"]
        self.assertLessEqual(out, cfg.MAX_OUT_PER_LEAD_PER_DAY)

    def test_chain_limit_per_week(self):
        conn, sms = fresh()
        phone = "79995550007"
        for _ in range(2):      # две цепочки: недозвон → отказ
            events.handle_call(conn, sms, phone, NOW, answered=False, source="sim")
            events.handle_sms(conn, sms, phone, "не надо", NOW)
        sent_before = len(sms.sent)
        events.handle_call(conn, sms, phone, NOW, answered=False, source="sim")
        self.assertEqual(len(sms.sent), sent_before)   # третья цепочка не открылась

    def test_blacklist(self):
        from callcatch import config as cfg
        conn, sms = fresh()
        cfg.BLACKLIST.add("79995550066")
        try:
            events.handle_call(conn, sms, "79995550066", NOW, answered=False, source="sim")
            self.assertEqual(len(sms.sent), 0)
        finally:
            cfg.BLACKLIST.discard("79995550066")

    def test_afterhours_text(self):
        conn, sms = fresh()
        sunday = datetime(2026, 7, 19, 12, 0)                  # воскресенье — выходной
        events.handle_call(conn, sms, "79995550009", sunday, answered=False, source="sim")
        self.assertIn("закрылись", sms.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
