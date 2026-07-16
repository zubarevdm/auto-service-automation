# -*- coding: utf-8 -*-
"""Нормализация телефонов к виду 79XXXXXXXXX."""
import re

_DIGITS = re.compile(r"\d+")


def norm_phone(raw) -> str | None:
    if raw is None:
        return None
    digits = "".join(_DIGITS.findall(str(raw)))
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "7" + digits
    return None
