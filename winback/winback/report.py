# -*- coding: utf-8 -*-
"""HTML-отчёты владельцу: «аудит спящих денег» (продажа) и итоги кампании
(продление/абонплата). Самодостаточный файл — открывается где угодно."""
from datetime import date
from pathlib import Path

from . import config as cfg

CSS = """
:root{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--bar:#2a78d6;--good:#006300;--ring:rgba(11,11,11,.10)}
@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;
--ink2:#c3c2b7;--grid:#2c2c2a;--bar:#3987e5;--good:#0ca30c;--ring:rgba(255,255,255,.10)}}
*{box-sizing:border-box;margin:0}
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--page);
color:var(--ink);padding:32px 16px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px}
.sub{color:var(--ink2);margin-bottom:24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:16px}
.tile .v{font-size:28px;font-weight:700}
.tile .l{color:var(--ink2);font-size:13px;margin-top:2px}
.tile .v.good{color:var(--good)}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
padding:20px;margin-bottom:20px}
.card h2{font-size:15px;margin-bottom:14px}
.row{display:grid;grid-template-columns:120px 1fr 170px;gap:10px;align-items:center;
padding:7px 0;border-bottom:1px solid var(--grid);font-size:14px}
.row:last-child{border-bottom:0}
.row .name{color:var(--ink2)}
.track{display:block;height:18px;position:relative}
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


def _page(title: str, sub: str, body: str) -> str:
    return (f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{CSS}</style></head><body><div class='wrap'>"
            f"<h1>{title}</h1><p class='sub'>{sub}</p>{body}"
            f"<p class='foot'>Сформировано автоматически · win-back система · "
            f"конверсии прогноза консервативные, по факту обычно выше</p></div></body></html>")


def _bars(rows: list[tuple[str, float, str]]) -> str:
    """Горизонтальные бары: (подпись, значение, текст значения)."""
    mx = max((v for _, v, _ in rows), default=1) or 1
    out = []
    for name, v, label in rows:
        w = round(v / mx * 100, 1)
        out.append(f"<div class='row'><span class='name'>{name}</span>"
                   f"<span class='track'><span class='fill' style='width:{w}%'></span></span>"
                   f"<span class='val'>{label}</span></div>")
    return "".join(out)


def audit_html(conn, seg_summary: dict, today: date, out: Path) -> Path:
    """Отчёт-аудит «сколько денег спит в вашей базе» — главный продажный документ."""
    total = conn.execute("SELECT COUNT(*) c FROM clients").fetchone()["c"]
    s = seg_summary
    tiles = f"""
    <div class='tiles'>
      <div class='tile'><div class='v'>{total}</div><div class='l'>клиентов в базе</div></div>
      <div class='tile'><div class='v'>{s['total_clients']}</div>
        <div class='l'>уснувших — с ними никто не работает</div></div>
      <div class='tile'><div class='v'>{round(s['forecast_visits'])}</div>
        <div class='l'>заездов вернёт первая кампания (прогноз)</div></div>
      <div class='tile'><div class='v good'>{money(s['forecast_revenue'])}</div>
        <div class='l'>выручки спит в базе (2 месяца)</div></div>
    </div>"""
    bars = _bars([(r["title"], r["count"],
                   f"{r['count']} чел · ~{money(r['forecast_revenue'])}")
                  for r in s["rows"]])
    body = tiles + (f"<div class='card'><h2>Кто эти люди — по сегментам</h2>{bars}"
                    f"<p class='note'>Каждому сегменту — свой повод и свой оффер: по пробегу — "
                    f"конкретное ТО, сезонным — переобувка, спящим — бесплатная диагностика, "
                    f"уходящим — персональная скидка.</p></div>")
    body += ("<div class='card'><h2>Модель оплаты</h2><p>Пилот 2 месяца: вы платите "
             f"<b>{int(cfg.COMMISSION * 100)}% от фактически возвращённой выручки</b>. "
             "Каждый возвращённый клиент подтверждается: касание → запись → заезд в CRM "
             "в течение 14 дней. Нет заездов — нет оплаты.</p></div>")
    out.write_text(_page("Аудит: спящие деньги в вашей базе",
                         f"по выгрузке из CRM · {today:%d.%m.%Y}", body), encoding="utf-8")
    return out


def campaign_html(conn, res: dict, seg_summary: dict, today: date, out: Path) -> Path:
    """Итоги кампании: возвращённые клиенты, выручка, комиссия."""
    tiles = f"""
    <div class='tiles'>
      <div class='tile'><div class='v'>{res['sent']}</div><div class='l'>касаний отправлено</div></div>
      <div class='tile'><div class='v'>{res['returned_clients']}</div>
        <div class='l'>клиентов вернулось (подтверждено заездом)</div></div>
      <div class='tile'><div class='v good'>{money(res['revenue'])}</div>
        <div class='l'>возвращённая выручка</div></div>
      <div class='tile'><div class='v'>{money(res['commission'])}</div>
        <div class='l'>наша комиссия ({int(cfg.COMMISSION * 100)}%)</div></div>
    </div>"""
    seg_rows = []
    for seg in cfg.SEGMENT_ORDER:
        r = res["by_segment"].get(seg)
        if r:
            seg_rows.append((cfg.SEGMENT_TITLES[seg], r["revenue"] or 0,
                             f"{r['clients']} чел · {money(r['revenue'] or 0)}"))
    body = tiles
    if seg_rows:
        body += (f"<div class='card'><h2>Возвращённая выручка по сегментам</h2>"
                 f"{_bars(seg_rows)}</div>")
    rows = conn.execute("""
        SELECT c.name, c.phone, t.segment, v.visit_date, a.amount
        FROM attributions a
        JOIN clients c ON c.id=a.client_id
        JOIN touches t ON t.id=a.touch_id
        JOIN visits v ON v.id=a.visit_id
        ORDER BY v.visit_date
    """).fetchall()
    trs = "".join(
        f"<tr><td>{r['name']}</td><td>+{r['phone']}</td>"
        f"<td>{cfg.SEGMENT_TITLES[r['segment']]}</td>"
        f"<td>{date.fromisoformat(r['visit_date']):%d.%m.%Y}</td>"
        f"<td>{money(r['amount'])}</td></tr>" for r in rows)
    body += (f"<div class='card'><h2>Каждый возвращённый клиент — поимённо</h2>"
             f"<table><tr><th>Клиент</th><th>Телефон</th><th>Сегмент</th>"
             f"<th>Заезд</th><th>Чек</th></tr>{trs}</table>"
             f"<p class='note'>Правило атрибуции: заезд в течение "
             f"{cfg.ATTRIBUTION_WINDOW_DAYS} дней после нашего касания.</p></div>")
    if seg_summary["total_clients"]:
        body += (f"<div class='card'><h2>Что дальше</h2><p>В базе остаётся "
                 f"<b>{seg_summary['total_clients']}</b> уснувших клиентов, и каждый месяц "
                 f"засыпают новые. Система работает непрерывно: сегментирует, касается, "
                 f"возвращает и отчитывается этим же отчётом.</p></div>")
    out.write_text(_page("Итоги win-back кампании",
                         f"отчёт на {today:%d.%m.%Y}", body), encoding="utf-8")
    return out
