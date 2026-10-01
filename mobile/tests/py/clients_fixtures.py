"""Исходные данные эталона клиентов в папке argv[1]: base.db и clients.xlsx."""
import os
import sys
from datetime import datetime

import openpyxl

from src.database import DatabaseManager
from src.models import Client, DeliveryPoint

OUT = sys.argv[1]
STAMP = datetime(2026, 9, 1, 10, 0, 0)
K4, K16 = DeliveryPoint.KOMSOMOLSKAYA_4, DeliveryPoint.KOLTSEVAYA_16

db = DatabaseManager(db_path=os.path.join(OUT, "base.db"))
db.create_tables()
s = db.get_session()
for oid, name, phone, point, active in [
    ("224933356", "Анна", None, K4, True),
    ("0147012251", "Борис", None, K16, True),      # хранится с ведущим нулём
    ("555000111", "Удалённый", None, K16, False),
    ("777000222", "Без изменений", "+7900", K16, True),
    ("900000001", "Для правки", None, K4, True),
    ("900000002", "Для удаления", None, K4, True),
]:
    s.add(Client(ozon_client_id=oid, full_name=name, phone=phone, fixed_delivery_point=point,
                 is_active=active, created_at=STAMP, updated_at=STAMP))
s.commit()
s.close()
db.engine.dispose()

wb = openpyxl.Workbook()
ws = wb.active
ws.append(["Список клиентов"])
ws.append(["Озон ID", "Имя", "Тел", "Точка по выдаче", "Ozon ID"])   # второй «Ozon ID» не берётся
for row in [
    [224933356.0, "Анна Н.", None, "Комсомольская 4"],               # float id, новое имя
    ["147012251", "Борис", None, "Кольцевая 16"],                    # канонизация id
    [555000111, "Удалённый", None, "кольцевая"],                     # реактивация
    ["777000222", "Без изменений", "+7900", "Кольцевая 16"],         # нет изменений
    ["0301234567", "Петров П.П.", None, " Комсомольская 4 "],        # новый
    ["301234567", "Петров П.П.", None, "Комсомольская 4"],           # тот же — молча
    ["0301234567", "Петров Другой", None, "Комсомольская 4"],        # конфликт
    [400000001, None, 79001234567, "КОЛЬЦЕВАЯ16"],                   # телефон числом
    ["12-34", "Плохой ID", None, "Комсомольская 4"],
    ["400000002", "Без точки", None, None],
    ["400000003", "Не та точка", None, "Ленина 1"],
    [None, "Пустой ID", None, "Комсомольская 4"],
    ["   ", "Пробелы", None, "Комсомольская 4"],
    ["400000004", "  С пробелами  ", "  +7 900  ", "Комсомольская"],
]:
    ws.append(row)
wb.save(os.path.join(OUT, "clients.xlsx"))
