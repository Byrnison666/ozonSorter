"""Массовый импорт клиентов из xlsx: happy path, обновление дублей, сбор ошибок."""
import os
import tempfile
import unittest

import openpyxl
from sqlalchemy import select

from src.database import DatabaseManager
from src.models import Client, DeliveryPoint, DeliveryPointPolicy
from src.client_import_service import ClientImportService, TEMPLATE_HEADERS


def _make_xlsx(rows, headers=TEMPLATE_HEADERS):
    path = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


class ClientImportTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseManager(db_path=self.db_path)
        self.db.create_tables()
        self.session = self.db.get_session()
        self.svc = ClientImportService(self.session)
        self._files = []

    def tearDown(self):
        self.session.close()
        os.remove(self.db_path)
        for f in self._files:
            if os.path.exists(f):
                os.remove(f)

    def _import(self, rows, headers=TEMPLATE_HEADERS):
        path = _make_xlsx(rows, headers)
        self._files.append(path)
        return self.svc.import_clients(path)

    def _client(self, ozon_id):
        return self.session.execute(
            select(Client).where(Client.ozon_client_id == ozon_id)
        ).scalar_one_or_none()

    def test_happy_path_fixed_and_manual(self):
        res = self._import([
            ["111", "Иванов", "+7", "FIXED", "Комсомольская 4"],
            ["222", "Петров", "", "MANUAL", ""],
        ])
        self.assertEqual((res.added, res.updated, len(res.errors)), (2, 0, 0))
        c1 = self._client("111")
        self.assertEqual(c1.delivery_point_policy, DeliveryPointPolicy.FIXED)
        self.assertEqual(c1.fixed_delivery_point, DeliveryPoint.KOMSOMOLSKAYA_4)
        c2 = self._client("222")
        self.assertEqual(c2.delivery_point_policy, DeliveryPointPolicy.MANUAL)
        self.assertIsNone(c2.fixed_delivery_point)

    def test_duplicate_updates_existing(self):
        self._import([["111", "Старое имя", "", "FIXED", "Комсомольская 4"]])
        res = self._import([["111", "Новое имя", "+7999", "MANUAL", ""]])
        self.assertEqual((res.added, res.updated), (0, 1))
        c = self._client("111")
        self.assertEqual(c.full_name, "Новое имя")
        self.assertEqual(c.phone, "+7999")
        self.assertEqual(c.delivery_point_policy, DeliveryPointPolicy.MANUAL)
        self.assertIsNone(c.fixed_delivery_point)
        # дубля в БД не появилось
        n = len(self.session.execute(
            select(Client).where(Client.ozon_client_id == "111")
        ).scalars().all())
        self.assertEqual(n, 1)

    def test_bad_rows_collected_good_rows_imported(self):
        res = self._import([
            ["abc", "", "", "FIXED", "Комсомольская 4"],   # id не цифры
            ["333", "", "", "FIXED", ""],                  # FIXED без точки
            ["444", "", "", "MANUAL", "Кольцевая 16"],     # MANUAL с точкой
            ["555", "", "", "ХЗ", ""],                     # неизвестная политика
            ["666", "", "", "FIXED", "Луна"],              # неизвестная точка
            ["777", "Ок", "", "FIXED", "Кольцевая 16"],    # валидная
        ])
        self.assertEqual(res.added, 1)
        self.assertEqual(len(res.errors), 5)
        self.assertIsNotNone(self._client("777"))
        self.assertIsNone(self._client("333"))
        # номера строк — реальные (данные начинаются со 2-й, заголовок 1-я)
        bad_rows = [r for r, _ in res.errors]
        self.assertEqual(bad_rows, [2, 3, 4, 5, 6])

    def test_numeric_id_coerced(self):
        # openpyxl вернёт int/float для числовой ячейки — не должно стать "111.0"
        res = self._import([[111, "", "", "FIXED", "Комсомольская 4"]])
        self.assertEqual(res.added, 1)
        self.assertIsNotNone(self._client("111"))

    def test_missing_required_header_raises(self):
        with self.assertRaises(ValueError):
            self._import([["111", "FIXED"]], headers=["ФИО", "Телефон"])

    def test_template_roundtrips(self):
        path = tempfile.mktemp(suffix=".xlsx")
        self._files.append(path)
        ClientImportService.write_template(path)
        res = self.svc.import_clients(path)
        # шаблон содержит 2 валидных примера
        self.assertEqual((res.added, len(res.errors)), (2, 0))


if __name__ == "__main__":
    unittest.main()
