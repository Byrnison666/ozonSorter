"""Массовый импорт клиентов из xlsx: happy path, обновление дублей, сбор ошибок.

Формат после удаления «Политики»: Ozon ID, ФИО, Телефон, Точка. Точка обязательна.
"""
import os
import tempfile
import unittest

import openpyxl
from sqlalchemy import select

from src.database import DatabaseManager
from src.models import Client, DeliveryPoint
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

    def test_happy_path(self):
        res = self._import([
            ["111", "Иванов", "+7", "Комсомольская 4"],
            ["222", "Петров", "", "Кольцевая 16"],
        ])
        self.assertEqual((res.added, res.updated, len(res.errors)), (2, 0, 0))
        self.assertEqual(self._client("111").fixed_delivery_point, DeliveryPoint.KOMSOMOLSKAYA_4)
        self.assertEqual(self._client("222").fixed_delivery_point, DeliveryPoint.KOLTSEVAYA_16)

    def test_duplicate_updates_existing(self):
        self._import([["111", "Старое имя", "", "Комсомольская 4"]])
        res = self._import([["111", "Новое имя", "+7999", "Кольцевая 16"]])
        self.assertEqual((res.added, res.updated), (0, 1))
        c = self._client("111")
        self.assertEqual(c.full_name, "Новое имя")
        self.assertEqual(c.phone, "+7999")
        self.assertEqual(c.fixed_delivery_point, DeliveryPoint.KOLTSEVAYA_16)
        n = len(self.session.execute(
            select(Client).where(Client.ozon_client_id == "111")
        ).scalars().all())
        self.assertEqual(n, 1)

    def test_bad_rows_collected_good_rows_imported(self):
        res = self._import([
            ["abc", "", "", "Комсомольская 4"],   # id не цифры
            ["333", "", "", ""],                  # нет точки
            ["444", "", "", "Луна"],              # неизвестная точка
            ["555", "Ок", "", "Кольцевая 16"],    # валидная
        ])
        self.assertEqual(res.added, 1)
        self.assertEqual(len(res.errors), 3)
        self.assertIsNotNone(self._client("555"))
        self.assertIsNone(self._client("333"))
        self.assertEqual([r for r, _ in res.errors], [2, 3, 4])

    def test_numeric_id_coerced(self):
        res = self._import([[111, "", "", "Комсомольская 4"]])
        self.assertEqual(res.added, 1)
        self.assertIsNotNone(self._client("111"))

    def test_missing_required_header_raises(self):
        with self.assertRaises(ValueError):
            self._import([["111", "x"]], headers=["ФИО", "Телефон"])

    def test_template_roundtrips(self):
        path = tempfile.mktemp(suffix=".xlsx")
        self._files.append(path)
        ClientImportService.write_template(path)
        res = self.svc.import_clients(path)
        self.assertEqual((res.added, len(res.errors)), (2, 0))


if __name__ == "__main__":
    unittest.main()
