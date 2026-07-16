# -*- coding: utf-8 -*-
"""CLI сервиса «Перехват».

  python -m callcatch demo                        — 3 недели жизни сервиса + отчёты
  python -m callcatch serve [--port 8040]         — сервер: вебхуки + дашборд
  python -m callcatch report [--days 7]           — HTML-отчёт владельцу
  python -m callcatch call <тел> [--answered]     — событие звонка руками (тест)
  python -m callcatch sms <тел> <текст>           — входящее SMS руками (тест)
  python -m callcatch audit <СТО> <answered|no_answer|busy> [--note ...]
                                                  — журнал «тайного аудита»
  python -m callcatch audit-report <СТО> [--calls-per-day 30]
                                                  — продажный отчёт по аудиту
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import db, demo as demo_mod, events, reports
from .normalize import norm_phone
from .sms import ConsoleSms, get_provider

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"


def _conn(name="callcatch.db"):
    DATA.mkdir(exist_ok=True)
    return db.connect(DATA / name)


def cmd_demo(args):
    OUT.mkdir(exist_ok=True)
    (DATA / "demo.db").unlink(missing_ok=True) if DATA.exists() else None
    DATA.mkdir(exist_ok=True)
    conn = db.connect(DATA / "demo.db")
    sms = ConsoleSms(OUT / "демо_sms_переписка.txt")
    (OUT / "демо_sms_переписка.txt").unlink(missing_ok=True)

    period = demo_mod.run(conn, sms)
    s = reports.stats(conn, period["start"], period["end"])
    print(f"[1/3] Симуляция {(period['end'] - period['start']).days} дн.: "
          f"{s['total']} звонков, пропущено {s['lost']} ({s['afterhours']} в нерабочее время)")
    print(f"[2/3] Перехват: SMS получили {s['contacted']}, ответили {s['replied']}, "
          f"записались {s['booked']}, спасено ~{s['revenue']:,.0f} ₽".replace(",", " "))

    out1 = reports.owner_report(conn, period["start"], period["end"], OUT / "демо_отчёт_владельцу.html")
    out2 = reports.audit_report(conn, "Демо-СТО «Гараж»", OUT / "демо_тайный_аудит.html")
    print(f"[3/3] Отчёты: {out1}")
    print(f"       {out2}")
    print(f"       переписка бота: {OUT / 'демо_sms_переписка.txt'}")
    print("Дашборд по демо-данным: python -m callcatch serve --db demo.db")


def cmd_serve(args):
    from . import server
    server.serve(DATA / (args.db or "callcatch.db"), args.port)


def cmd_report(args):
    conn = _conn(args.db or "callcatch.db")
    OUT.mkdir(exist_ok=True)
    now = datetime.now()
    out = reports.owner_report(conn, now - timedelta(days=args.days), now, OUT / "отчёт_владельцу.html")
    print(f"Отчёт: {out}")


def cmd_call(args):
    conn = _conn(args.db or "callcatch.db")
    phone = norm_phone(args.phone) or sys.exit("Кривой номер")
    res = events.handle_call(conn, get_provider(DATA / "sms_outbox.txt"), phone,
                             datetime.now(), args.answered)
    print(res)


def cmd_sms(args):
    conn = _conn(args.db or "callcatch.db")
    phone = norm_phone(args.phone) or sys.exit("Кривой номер")
    reply = events.handle_sms(conn, get_provider(DATA / "sms_outbox.txt"), phone,
                              args.text, datetime.now())
    print(f"Бот ответил: {reply}")


def cmd_audit(args):
    conn = _conn("audit.db")
    conn.execute("INSERT INTO audit_calls(target, at, result, note) VALUES(?,?,?,?)",
                 (args.target, datetime.now().strftime("%Y-%m-%d %H:%M"), args.result, args.note))
    conn.commit()
    n = conn.execute("SELECT COUNT(*) c FROM audit_calls WHERE target=?", (args.target,)).fetchone()["c"]
    print(f"Записано. По «{args.target}» звонков в журнале: {n}")


def cmd_audit_report(args):
    conn = _conn("audit.db")
    OUT.mkdir(exist_ok=True)
    safe = "".join(c for c in args.target if c.isalnum() or c in " -_")[:40].strip() or "аудит"
    out = reports.audit_report(conn, args.target, OUT / f"аудит_{safe}.html",
                               est_calls_per_day=args.calls_per_day)
    print(f"Продажный отчёт: {out}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="callcatch", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def sp_with_db(name):
        s = sub.add_parser(name)
        s.add_argument("--db", help="имя файла базы в data/ (по умолчанию callcatch.db)")
        return s

    sub.add_parser("demo")
    sub.add_parser("voice-sim")
    sp = sp_with_db("serve")
    sp.add_argument("--port", type=int)
    sp = sp_with_db("report")
    sp.add_argument("--days", type=int, default=7)
    sp = sp_with_db("call")
    sp.add_argument("phone")
    sp.add_argument("--answered", action="store_true")
    sp = sp_with_db("sms")
    sp.add_argument("phone")
    sp.add_argument("text")
    sp = sub.add_parser("audit")
    sp.add_argument("target")
    sp.add_argument("result", choices=["answered", "no_answer", "busy", "voicemail"])
    sp.add_argument("--note", default="")
    sp = sub.add_parser("audit-report")
    sp.add_argument("target")
    sp.add_argument("--calls-per-day", type=int, default=30)

    args = p.parse_args()
    {"demo": cmd_demo, "serve": cmd_serve, "report": cmd_report, "call": cmd_call,
     "sms": cmd_sms, "audit": cmd_audit, "audit-report": cmd_audit_report,
     "voice-sim": lambda a: __import__("callcatch.voicesim", fromlist=["run_all"]).run_all(),
     }[args.cmd](args)


if __name__ == "__main__":
    main()
