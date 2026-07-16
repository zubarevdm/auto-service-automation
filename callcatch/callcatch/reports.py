# -*- coding: utf-8 -*-
"""HTML-отчёты: еженедельный отчёт владельцу (продление) и
«тайный аудит» для холодной продажи."""
from datetime import datetime, timedelta
from pathlib import Path

from . import config as cfg

CSS = """
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--bar:#2a78d6;--good:#006300;--bad:#d03b3b;--ring:rgba(11,11,11,.10)}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;
--ink2:#c3c2b7;--grid:#2c2c2a;--bar:#3987e5;--good:#0ca30c;--bad:#e66767;--ring:rgba(255,255,255,.10)}}
*{box-sizing:border-box;margin:0}
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--page);
color:var(--ink);padding:32px 16px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px}
.sub{color:var(--ink2);margin-bottom:24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:24px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:16px}
.tile .v{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}
.tile .l{color:var(--ink2);font-size:13px;margin-top:2px}
.tile .v.good{color:var(--good)} .tile .v.bad{color:var(--bad)}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:20px;margin-bottom:20px}
.card h2{font-size:15px;margin-bottom:14px}
.row{display:grid;grid-template-columns:150px 1fr 130px;gap:10px;align-items:center;
padding:7px 0;border-bottom:1px solid var(--grid);font-size:14px}
.row:last-child{border-bottom:0}
.row .name{color:var(--ink2)}
.track{display:block;height:18px}
.fill{display:block;background:var(--bar);height:18px;border-radius:0 4px 4px 0;min-width:2px}
.val{text-align:right;font-variant-numeric:tabular-nums}
.note{color:var(--muted);font-size:13px;margin-top:12px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:500;padding:6px 8px;border-bottom:1px solid var(--grid)}
td{padding:6px 8px;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
.foot{color:var(--muted);font-size:12px;margin-top:24px}
"""


def money(x) -> str:
    return f"{round(x):,}".replace(",", " ") + " ₽"


def page(title: str, sub: str, body: str) -> str:
    return (f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{CSS}</style></head><body><div class='wrap'>"
            f"<h1>{title}</h1><p class='sub'>{sub}</p>{body}"
            f"<p class='foot'>Сформировано автоматически · сервис «Перехват»</p></div></body></html>")


def bars(rows) -> str:
    mx = max((v for _, v, _ in rows), default=1) or 1
    return "".join(
        f"<div class='row'><span class='name'>{name}</span>"
        f"<span class='track'><span class='fill' style='width:{round(v / mx * 100, 1)}%'></span></span>"
        f"<span class='val'>{label}</span></div>"
        for name, v, label in rows)


def stats(conn, since: datetime, until: datetime) -> dict:
    """Все цифры отчёта/дашборда за период."""
    a, b = since.strftime("%Y-%m-%d %H:%M"), until.strftime("%Y-%m-%d %H:%M")
    q = lambda sql, *p: conn.execute(sql, p).fetchone()  # noqa: E731
    total = q("SELECT COUNT(*) c FROM calls WHERE at BETWEEN ? AND ?", a, b)["c"]
    answered = q("SELECT COUNT(*) c FROM calls WHERE status='answered' AND at BETWEEN ? AND ?", a, b)["c"]
    missed = q("SELECT COUNT(*) c FROM calls WHERE status='missed' AND at BETWEEN ? AND ?", a, b)["c"]
    after = q("SELECT COUNT(*) c FROM calls WHERE status='afterhours' AND at BETWEEN ? AND ?", a, b)["c"]
    contacted = q("""SELECT COUNT(DISTINCT l.id) c FROM leads l
                     JOIN messages m ON m.lead_id=l.id AND m.direction='out'
                     WHERE l.created_at BETWEEN ? AND ?""", a, b)["c"]
    replied = q("""SELECT COUNT(DISTINCT l.id) c FROM leads l
                   JOIN messages m ON m.lead_id=l.id AND m.direction='in'
                   WHERE l.created_at BETWEEN ? AND ?""", a, b)["c"]
    booked = q("""SELECT COUNT(*) c, IFNULL(SUM(price_est),0) s FROM bookings b
                  JOIN leads l ON l.id=b.lead_id WHERE l.created_at BETWEEN ? AND ?""", a, b)
    handoff = q("SELECT COUNT(*) c FROM leads WHERE state='handoff' AND created_at BETWEEN ? AND ?", a, b)["c"]
    return {
        "total": total, "answered": answered, "missed": missed, "afterhours": after,
        "lost": missed + after, "contacted": contacted, "replied": replied,
        "booked": booked["c"], "revenue": booked["s"], "handoff": handoff,
    }


def owner_report(conn, since: datetime, until: datetime, out: Path) -> Path:
    s = stats(conn, since, until)
    lost_share = round(s["lost"] / s["total"] * 100) if s["total"] else 0
    tiles = f"""
    <div class='tiles'>
      <div class='tile'><div class='v'>{s['total']}</div><div class='l'>звонков всего</div></div>
      <div class='tile'><div class='v bad'>{s['lost']}</div>
        <div class='l'>не отвечено ({lost_share}% — в т.ч. {s['afterhours']} в нерабочее время)</div></div>
      <div class='tile'><div class='v'>{s['booked']}</div>
        <div class='l'>записей сделал Перехват из этих «потерянных»</div></div>
      <div class='tile'><div class='v good'>{money(s['revenue'])}</div>
        <div class='l'>спасённая выручка (оценка по прайсу)</div></div>
    </div>"""
    funnel = bars([
        ("Пропущено", s["lost"], f"{s['lost']}"),
        ("Догнали SMS", s["contacted"], f"{s['contacted']}"),
        ("Ответили", s["replied"], f"{s['replied']}"),
        ("Записались", s["booked"], f"{s['booked']}"),
    ])
    body = tiles + (f"<div class='card'><h2>Воронка перехвата</h2>{funnel}"
                    f"<p class='note'>Каждый пропущенный звонок получает SMS в течение минуты. "
                    f"{s['handoff']} диалогов передано администратору.</p></div>")
    rows = conn.execute("""
        SELECT b.phone, b.service, b.slot, b.price_est, b.status
        FROM bookings b JOIN leads l ON l.id=b.lead_id
        WHERE l.created_at BETWEEN ? AND ? ORDER BY b.slot""",
        (since.strftime("%Y-%m-%d %H:%M"), until.strftime("%Y-%m-%d %H:%M"))).fetchall()
    status_ru = {"new": "записан", "visited": "приехал", "no_show": "не приехал"}
    trs = "".join(f"<tr><td>+{r['phone']}</td><td>{r['service']}</td><td>{r['slot']}</td>"
                  f"<td>{money(r['price_est'])}</td><td>{status_ru.get(r['status'], r['status'])}</td></tr>"
                  for r in rows)
    body += (f"<div class='card'><h2>Каждая запись — поимённо</h2>"
             f"<table><tr><th>Телефон</th><th>Услуга</th><th>Время</th><th>Чек (оценка)</th>"
             f"<th>Статус</th></tr>{trs}</table>"
             "<p class='note'>Журнал записей ведётся на нашей стороне в момент подтверждения "
             "клиентом — сверяйте с вашей CRM.</p></div>")
    out.write_text(page(f"Перехват: отчёт для {cfg.SERVICE_NAME}",
                        f"{since:%d.%m.%Y} — {until:%d.%m.%Y}", body), encoding="utf-8")
    return out


def audit_report(conn, target: str, out: Path,
                 est_calls_per_day: int = 30, avg_check: int = 8000) -> Path:
    """«Тайный аудит» — продажный документ по холодному сервису."""
    rows = conn.execute("SELECT at, result, note FROM audit_calls WHERE target=? ORDER BY at",
                        (target,)).fetchall()
    total = len(rows)
    missed = sum(1 for r in rows if r["result"] != "answered")
    share = missed / total if total else 0
    monthly_lost = round(est_calls_per_day * 30 * share)
    saved = round(monthly_lost * 0.33 * 0.5)          # перехват трети, конверсия 50%
    result_ru = {"answered": "ответили", "no_answer": "не взяли трубку",
                 "busy": "занято", "voicemail": "автоответчик"}
    trs = "".join(f"<tr><td>{r['at']}</td><td>{result_ru.get(r['result'], r['result'])}</td>"
                  f"<td>{r['note'] or ''}</td></tr>" for r in rows)
    tiles = f"""
    <div class='tiles'>
      <div class='tile'><div class='v'>{total}</div><div class='l'>тестовых звонков сделано</div></div>
      <div class='tile'><div class='v bad'>{missed}</div><div class='l'>остались без ответа</div></div>
      <div class='tile'><div class='v bad'>{round(share * 100)}%</div><div class='l'>обращений теряется</div></div>
      <div class='tile'><div class='v good'>{money(saved * avg_check)}</div>
        <div class='l'>выручки в месяц можно спасать (оценка)</div></div>
    </div>"""
    body = tiles + (f"<div class='card'><h2>Журнал тестовых звонков</h2>"
                    f"<table><tr><th>Когда</th><th>Результат</th><th>Комментарий</th></tr>{trs}</table>"
                    f"<p class='note'>Оценка: при ~{est_calls_per_day} звонках в день доля потерь "
                    f"{round(share * 100)}% даёт ~{monthly_lost} потерянных обращений в месяц; "
                    f"перехват трети из них с конверсией 50% и чеком {money(avg_check)} — "
                    f"{money(saved * avg_check)}/мес.</p></div>"
                    "<div class='card'><h2>Что предлагаем</h2><p>Переадресация непринятых звонков "
                    "на умный номер: клиент в течение минуты получает SMS от вашего имени, бот "
                    "записывает его в свободный слот, администратор получает уведомление. "
                    "Еженедельный отчёт — каждая спасённая запись поимённо. "
                    "Внедрение — одна настройка у оператора, без изменений в вашей работе.</p></div>")
    out.write_text(page(f"Аудит входящих звонков: {target}",
                        "тайный покупатель · телефонная доступность", body), encoding="utf-8")
    return out
