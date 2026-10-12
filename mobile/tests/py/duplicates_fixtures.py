"""Исходные данные эталона дублей в папке argv[1]: base.db (база ПК с клиентами)
и отчёты day1..5.xlsx."""
import os
import sys
from datetime import datetime

import openpyxl

from src.database import DatabaseManager
from src.models import Client, DeliveryPoint

OUT = sys.argv[1]
HEADER = ["Этикетка\nНазвание", "Номер отправления", "Тип", "Статус", "Ячейка"]
READY = "Готово к выдаче"
STAMP = datetime(2026, 9, 1, 10, 0, 0)

ISSUED = ["111-A-1", "111-A-2", "222-B-1", "111-C-1"]
OTHERS = [f"111-OTH-{i}" for i in range(1, 10)]
STRANGERS = [f"999-X-{i}" for i in range(1, 6)]
ALL = ISSUED + ["111-NEW-1"] + OTHERS + STRANGERS


def base_db():
    db = DatabaseManager(db_path=os.path.join(OUT, "base.db"))
    db.create_tables()
    s = db.get_session()
    for oid, name, point in [
        ("111", "Анна", DeliveryPoint.KOMSOMOLSKAYA_4),
        ("222", "Борис", DeliveryPoint.KOLTSEVAYA_16),
    ]:
        s.add(Client(ozon_client_id=oid, full_name=name, fixed_delivery_point=point,
                     phone="+7 900 000-00-00", created_at=STAMP, updated_at=STAMP))
    s.commit()
    s.close()
    db.engine.dispose()


def report(name, postings):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    for p in postings:
        # Этикетка и ячейка постоянны у посылки: это одна и та же посылка в разных отчётах.
        cell = f"A-{ALL.index(p) + 1}"
        ws.append([f"LBL-{p}\nТовар {p}", p, "Отправление", READY, cell])
    # КТЯ — тоже строка отчёта: учитывается в проверке неполного отчёта.
    ws.append(["ii17574018168\nКТЯ", "ii17574018168", "Отправление", READY, "Z-1"])
    wb.save(os.path.join(OUT, name))


base_db()
report("day1.xlsx", ISSUED + OTHERS + STRANGERS)
report("day2.xlsx", OTHERS + STRANGERS)                        # ISSUED выданы
report("day3.xlsx", ISSUED + ["111-NEW-1"] + OTHERS + STRANGERS)
report("day4.xlsx", ["111-NEW-1"])                              # неполный
report("day5.xlsx", ISSUED + ["111-NEW-1"] + OTHERS + STRANGERS)
