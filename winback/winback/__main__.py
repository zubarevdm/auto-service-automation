# -*- coding: utf-8 -*-
"""CLI win-back системы.

  python -m winback demo                    — полный демо-прогон на синтетике
  python -m winback audit <выгрузка.xlsx>   — аудит «спящих денег» (продажа)
  python -m winback import <выгрузка.xlsx>  — импорт выгрузки в рабочую базу
  python -m winback segment                 — сводка по сегментам в консоль
  python -m winback messages                — план касаний + Excel на отправку
  python -m winback send                    — выгрузить сообщения на сегодня
  python -m winback attribute <свежая.xlsx> — атрибуция заездов по свежей выгрузке
  python -m winback report                  — HTML-отчёт по кампании
  python -m winback stop <телефон>          — клиент ответил/отписался: стоп цепочки
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from . import attribution, config as cfg, db, demo as demo_mod, ingest, messages, report, segment

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"


def _conn(db_path=None):
    DATA.mkdir(exist_ok=True)
    return db.connect(db_path or DATA / "winback.db")


def cmd_import(args):
    conn = _conn(args.db)
    st = ingest.import_file(conn, args.file)
    print(f"Импорт: строк {st['rows']}, новых визитов {st['new_visits']}, "
          f"пропущено (нет телефона/даты) {st['skipped']}.")
    print(f"В базе: клиентов {st['clients']}, визитов {st['visits']}.")


def cmd_segment(args, today=None):
    conn = _conn(args.db)
    today = today or date.today()
    seg = segment.segment_all(conn, today)
    s = segment.summary(seg)
    print(f"Уснувших клиентов: {s['total_clients']}")
    for r in s["rows"]:
        print(f"  {r['title']:<12} {r['count']:>4} чел · средний чек {r['avg_check']:>7.0f} ₽ · "
              f"прогноз {r['forecast_visits']:.0f} заездов / {r['forecast_revenue']:>9,.0f} ₽"
              .replace(",", " "))
    print(f"Итого прогноз первой волны: {s['forecast_visits']:.0f} заездов, "
          f"{s['forecast_revenue']:,.0f} ₽".replace(",", " "))
    return conn, seg, s


def cmd_audit(args):
    """Разовый аудит чужой выгрузки: отдельная временная база, только прогноз."""
    tmp = DATA / "_audit.db"
    tmp.unlink(missing_ok=True)
    conn = _conn(tmp)
    st = ingest.import_file(conn, args.file)
    today = date.today()
    seg = segment.segment_all(conn, today)
    s = segment.summary(seg)
    OUT.mkdir(exist_ok=True)
    out = report.audit_html(conn, s, today, OUT / "аудит_спящих_денег.html")
    print(f"База: {st['clients']} клиентов, {st['visits']} визитов. "
          f"Уснувших: {s['total_clients']}.")
    print(f"Прогноз: {s['forecast_visits']:.0f} заездов, "
          f"{s['forecast_revenue']:,.0f} ₽.".replace(",", " "))
    print(f"Отчёт для показа владельцу: {out}")


def cmd_messages(args, today=None):
    conn = _conn(args.db)
    today = today or date.today()
    seg = segment.segment_all(conn, today)
    n = messages.plan_touches(conn, seg, today)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"сообщения_{today.isoformat()}.xlsx"
    k = messages.export_today(conn, out, today)
    print(f"Цепочки поставлены {n} клиентам. Сообщений на сегодня: {k}.")
    print(f"Файл на отправку: {out}")


def cmd_send(args, today=None):
    conn = _conn(args.db)
    today = today or date.today()
    OUT.mkdir(exist_ok=True)
    out = OUT / f"сообщения_{today.isoformat()}.xlsx"
    k = messages.export_today(conn, out, today)
    print(f"Сообщений на сегодня: {k}. Файл: {out}" if k else "На сегодня сообщений нет.")


def cmd_attribute(args, today=None):
    conn = _conn(args.db)
    ingest.import_file(conn, args.file)
    res = attribution.match(conn)
    print(f"Новых совпадений: {res['new_matches']}. Всего возвращено: "
          f"{res['returned_clients']} клиентов, {res['revenue']:,.0f} ₽ выручки, "
          f"комиссия {res['commission']:,.0f} ₽.".replace(",", " "))


def cmd_report(args, today=None):
    conn = _conn(args.db)
    today = today or date.today()
    res = attribution.results(conn)
    s = segment.summary(segment.segment_all(conn, today))
    OUT.mkdir(exist_ok=True)
    out = report.campaign_html(conn, res, s, today, OUT / "отчёт_кампании.html")
    print(f"Отчёт: {out}")


def cmd_stop(args):
    from .normalize import norm_phone
    conn = _conn(args.db)
    phone = norm_phone(args.phone)
    row = conn.execute("SELECT id, name FROM clients WHERE phone=?", (phone,)).fetchone()
    if not row:
        sys.exit(f"Клиент с телефоном {args.phone} не найден.")
    messages.stop_chain(conn, row["id"])
    if args.optout:
        conn.execute("UPDATE clients SET consent=0 WHERE id=?", (row["id"],))
        conn.commit()
    print(f"Цепочка остановлена: {row['name']} (+{phone})"
          + (", клиент отписан от рассылок навсегда." if args.optout else "."))


def cmd_demo(args):
    """Полный прогон на синтетической базе: как будто кампания шла последний месяц."""
    DATA.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    demo_db = DATA / "demo.db"
    demo_db.unlink(missing_ok=True)
    conn = db.connect(demo_db)
    as_of = date.today() - timedelta(days=30)

    base = OUT / "демо_выгрузка_CRM.xlsx"
    demo_mod.gen_base(base, end=as_of)
    st = ingest.import_file(conn, base)
    print(f"[1/5] Демо-база: {st['clients']} клиентов, {st['visits']} визитов → {base.name}")

    seg = segment.segment_all(conn, as_of)
    s = segment.summary(seg)
    print(f"[2/5] Сегментация: {s['total_clients']} уснувших, прогноз "
          f"{s['forecast_revenue']:,.0f} ₽".replace(",", " "))
    report.audit_html(conn, s, as_of, OUT / "демо_аудит.html")

    n = messages.plan_touches(conn, seg, as_of)
    sent = 0
    for wave, shift in enumerate((0, 6, 13), 1):
        sent += messages.export_today(conn, OUT / f"демо_сообщения_волна{wave}.xlsx",
                                      as_of + timedelta(days=shift))
    print(f"[3/5] Касания: цепочки {n} клиентам, отправлено {sent} сообщений")

    fresh = OUT / "демо_свежая_выгрузка.xlsx"
    k = demo_mod.gen_returns(conn, fresh, as_of)
    ingest.import_file(conn, fresh)
    res = attribution.match(conn)
    print(f"[4/5] Атрибуция: вернулось {res['returned_clients']} клиентов, "
          f"выручка {res['revenue']:,.0f} ₽, комиссия {res['commission']:,.0f} ₽"
          .replace(",", " "))

    s2 = segment.summary(segment.segment_all(conn, date.today()))
    out = report.campaign_html(conn, res, s2, date.today(), OUT / "демо_отчёт_кампании.html")
    print(f"[5/5] Отчёты: {OUT / 'демо_аудит.html'}")
    print(f"       {out}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="winback", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="путь к базе (по умолчанию data/winback.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("demo", "segment", "messages", "send", "report"):
        sub.add_parser(name)
    for name in ("import", "audit", "attribute"):
        sp = sub.add_parser(name)
        sp.add_argument("file", help="Excel-выгрузка из CRM")
    sp = sub.add_parser("stop")
    sp.add_argument("phone")
    sp.add_argument("--optout", action="store_true", help="полная отписка (согласие=нет)")

    args = p.parse_args()
    {"demo": cmd_demo, "import": cmd_import, "audit": cmd_audit,
     "segment": lambda a: cmd_segment(a) and None, "messages": cmd_messages,
     "send": cmd_send, "attribute": cmd_attribute, "report": cmd_report,
     "stop": cmd_stop}[args.cmd](args)


if __name__ == "__main__":
    main()
