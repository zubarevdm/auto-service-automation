# -*- coding: utf-8 -*-
"""Нормализация грязных данных из выгрузок: телефоны, даты, пробеги."""
import re
from datetime import date, datetime

PHONE_RE = re.compile(r"\d+")


def norm_phone(raw) -> str | None:
    """Приводит к виду 79XXXXXXXXX. Мусор и короткие номера отбрасывает."""
    if raw is None:
        return None
    digits = "".join(PHONE_RE.findall(str(raw)))
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "7" + digits
    return None


def norm_name(raw) -> str:
    if not raw:
        return ""
    # Из «Иванов Иван Иванович» берём имя для обращения, если угадывается
    parts = str(raw).strip().split()
    if len(parts) >= 2 and len(parts[1]) > 2:
        return parts[1].capitalize()  # ФИО → имя
    return parts[0].capitalize() if parts else ""


DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S")


def norm_date(raw) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def norm_int(raw) -> int | None:
    if raw is None or raw == "":
        return None
    digits = "".join(PHONE_RE.findall(str(raw)))
    return int(digits) if digits else None


def norm_amount(raw) -> float:
    if raw is None or raw == "":
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^\d,.\-]", "", str(raw)).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0
