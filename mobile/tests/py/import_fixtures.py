"""Исходные данные эталона импорта в папке argv[1]: base.db (база ПК с
клиентами) и отчёты report1..3.xlsx."""
import os
import sys
from datetime import datetime

import openpyxl

from src.database import DatabaseManager
from src.models import Client, DeliveryPoint

OUT = sys.argv[1]
OLD_HEADER = ["Этикетка\nНазвание", "Номер отправления", "Тип", "Статус", "Ячейка",
              "Отсчётная дата отправки"]
NEW_HEADER = ["Этикетка\nНазвание", "Номер отправления", "Ячейка"]
READY, TO_WH = "Готово к выдаче", "Отправить на склад"
STAMP = datetime(2026, 9, 1, 10, 0, 0)


def base_db():
    db = DatabaseManager(db_path=os.path.join(OUT, "base.db"))
    db.create_tables()
    s = db.get_session()
    for oid, name, point, active in [
        ("224933356", "Анна", DeliveryPoint.KOMSOMOLSKAYA_4, True),
        ("147012251", "Борис", DeliveryPoint.KOLTSEVAYA_16, True),
        ("555000111", "Неактивный", DeliveryPoint.KOLTSEVAYA_16, False),
        ("777000222", "Без точки", None, True),
    ]:
        s.add(Client(ozon_client_id=oid, full_name=name, fixed_delivery_point=point,
                     is_active=active, created_at=STAMP, updated_at=STAMP))
    s.commit()
    s.close()
    db.engine.dispose()


def report(name, header, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Отчёт склада"])
    ws.append([])
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(os.path.join(OUT, name))


base_db()

report("report1.xlsx", OLD_HEADER, [
    ["a1\nЧашка", "0224933356-0001-1", "Отправление", READY, "10-1", datetime(2026, 9, 1, 9, 0)],
    ["a2\nЛожка", "224933356-0001-2", "Отправление", TO_WH, "10-2", None],
    ["b1\nКнига", "147012251-0002-1", "Отправление\nПовреждено", READY, "11-1", 45500.5],
    ["b2\nРучка", "147012251-0002-2", "Отправление", READY, "11-2", None],
    ["b3\nТетрадь", "147012251-0002-3", "Отправление", READY, "11-3", None],
    ["c1\nЧужое", "999999999-0003-1", "Отправление", READY, "12-1", None],
    ["e1\nБудущий клиент", "888000333-0004-1", "Отправление", READY, "12-2", None],
    ["e2\nБудущий возврат", "888000333-0004-2", "Отправление", TO_WH, "12-3", None],
    ["k1\nКТЯ", "ii17574018168", "Отправление", READY, "13-1", None],
    ["n1\nНеактивный", "555000111-0005-1", "Отправление", READY, "14-1", None],
    ["p1\nБез точки", "777000222-0006-1", "Отправление", READY, "15-1", None],
    ["a1\nЧашка", "0224933356-0001-1", "Отправление", READY, "10-1", None],   # дубль строки
    [None, "224933356-0001-3", "Отправление", READY, 42, None],                # без этикетки
])

report("report2.xlsx", NEW_HEADER, [
    ["a1\nЧашка", "0224933356-0001-1", "20-1"],             # ячейку переложили
    ["a2\nЛожка", "224933356-0001-2", "10-2"],               # возврат снова готов
    ["b1\nКнига", "147012251-0002-1", "   "],                # пустая ячейка не затирает
    ["b2\nРучка", "147012251-0002-2", "На проверку-1"],      # стала возвратом
    ["b3\nТетрадь", "147012251-0002-3", "11-3"],             # на точке
    ["b4\nДоставленная", "147012251-0002-4", "11-4"],        # новая, готова
    ["e1\nБудущий клиент", "888000333-0004-1", "12-2"],      # клиента добавили
    ["e2\nБудущий возврат", "888000333-0004-2", "На проверку-2"],
    ["k1\nКТЯ", "ii17574018168", "13-2"],
    ["a1-другой\nЧашка 2", "0224933356-0001-1", "20-2"],     # тот же номер, другой штрихкод
    ["x\nЧисловой номер", 224933356, "1-1"],                 # не парсится как клиент → КТЯ
])

report("report3_bad_date.xlsx", OLD_HEADER, [
    ["a9\nНовая", "224933356-0009-1", "Отправление", READY, "30-1", "30.09.2026"],
])
