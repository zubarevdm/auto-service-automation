# -*- coding: utf-8 -*-
"""Демо-режим: синтетическая база автосервиса + полный прогон конвейера.
Нужен, чтобы показывать систему владельцу до передачи реальной выгрузки."""
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

FIRST = ["Иван", "Дмитрий", "Сергей", "Алексей", "Андрей", "Михаил", "Олег", "Павел",
         "Николай", "Владимир", "Артём", "Максим", "Егор", "Роман", "Виктор",
         "Анна", "Елена", "Ольга", "Наталья", "Марина", "Татьяна", "Ирина", "Светлана"]
LAST = ["Иванов", "Петров", "Смирнов", "Кузнецов", "Соколов", "Попов", "Лебедев",
        "Козлов", "Новиков", "Морозов", "Волков", "Соловьёв", "Васильев", "Зайцев",
        "Павлов", "Семёнов", "Голубев", "Виноградов", "Богданов", "Воробьёв"]
CARS = [("Lada", "Vesta"), ("Lada", "Granta"), ("Kia", "Rio"), ("Hyundai", "Solaris"),
        ("Hyundai", "Creta"), ("Toyota", "Camry"), ("Toyota", "RAV4"), ("Kia", "Sportage"),
        ("Volkswagen", "Polo"), ("Skoda", "Octavia"), ("Renault", "Duster"),
        ("Nissan", "Qashqai"), ("Haval", "Jolion"), ("Chery", "Tiggo 7"), ("Geely", "Coolray"),
        ("Mazda", "CX-5"), ("Ford", "Focus"), ("Mitsubishi", "Outlander")]
SERVICES = [("Замена масла и фильтров", 4500, 7000), ("ТО плановое", 8000, 16000),
            ("Диагностика подвески", 1500, 3000), ("Замена колодок", 4000, 9000),
            ("Шиномонтаж, переобувка", 2500, 5000), ("Развал-схождение", 2500, 4500),
            ("Замена ремня ГРМ", 12000, 25000), ("Ремонт тормозной системы", 6000, 18000),
            ("Замена амортизаторов", 9000, 22000), ("Кондиционер: заправка", 3000, 6000)]


def gen_base(path: Path, end: date, n_clients: int = 1400, seed: int = 42) -> None:
    """Пишет Excel-выгрузку «как из CRM»: строка = заказ-наряд."""
    rng = random.Random(seed)
    wb = Workbook()
    ws = wb.active
    ws.append(["Клиент", "Телефон", "Марка", "Модель", "Год выпуска",
               "Дата заказ-наряда", "Выполненные работы", "Пробег", "Сумма, руб."])

    for i in range(n_clients):
        name = f"{rng.choice(LAST)} {rng.choice(FIRST)}"
        phone = f"+7 (9{rng.randint(10, 99)}) {rng.randint(100, 999)}-{i:04d}"
        brand, model = rng.choice(CARS)
        year = rng.randint(2008, 2024)
        n_visits = rng.choices([1, 2, 3, 4, 5, 6, 8], [25, 20, 18, 14, 10, 8, 5])[0]
        # Давность последнего визита: часть базы активна, часть спит, часть потеряна
        days_back = rng.choices(
            [rng.randint(10, 120), rng.randint(150, 300),
             rng.randint(300, 450), rng.randint(450, 900)],
            [40, 28, 17, 15])[0]
        interval = rng.randint(70, 220)
        mileage = rng.randint(30, 180) * 1000
        daily = rng.randint(25, 80)

        d = end - timedelta(days=days_back)
        for v in range(n_visits):
            svc, lo, hi = rng.choice(SERVICES)
            row_mileage = mileage if rng.random() < 0.75 else None
            ws.append([name, phone, brand, model, year, d.strftime("%d.%m.%Y"),
                       svc, row_mileage, rng.randint(lo, hi)])
            step = interval + rng.randint(-25, 25)
            d -= timedelta(days=step)
            mileage -= daily * step
            if d.year < end.year - 3 or mileage < 5000:
                break
    wb.save(str(path))


def gen_returns(conn, path: Path, as_of: date, seed: int = 7) -> int:
    """Симулирует ответ рынка: часть клиентов, получивших касания,
    «заезжает» через 2–13 дней. Пишет свежую выгрузку для атрибуции."""
    from . import config as cfg
    rng = random.Random(seed)
    touched = conn.execute("""
        SELECT DISTINCT t.client_id, t.segment, c.name, c.phone,
               ca.brand, ca.model, ca.year
        FROM touches t
        JOIN clients c ON c.id = t.client_id
        LEFT JOIN cars ca ON ca.client_id = c.id
        WHERE t.step = 1
    """).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.append(["Клиент", "Телефон", "Марка", "Модель", "Год выпуска",
               "Дата заказ-наряда", "Выполненные работы", "Пробег", "Сумма, руб."])
    n = 0
    for t in touched:
        # вероятность возврата = конверсия сегмента * доходимость
        if rng.random() < cfg.CONVERSION[t["segment"]] * cfg.SHOW_RATE * 1.15:
            svc, lo, hi = rng.choice(SERVICES)
            visit = as_of + timedelta(days=rng.randint(2, 13))
            # win-back чек выше среднего: накопленные работы
            ws.append([t["name"], f"+{t['phone']}", t["brand"], t["model"], t["year"],
                       visit.strftime("%d.%m.%Y"), svc, None,
                       round(rng.randint(lo, hi) * 1.35)])
            n += 1
    wb.save(str(path))
    return n
