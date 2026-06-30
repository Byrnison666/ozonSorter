"""Импорт клиентов: канонизация Ozon ID и устойчивость к дублям/наложениям.

Матчинг посылок идёт по нормализованному Ozon ID (без ведущих нулей), поэтому
«0224933356» и «224933356» — один клиент. Импорт обязан:
  - не плодить два клиента на один нормализованный id (иначе матчинг молча
    отдаёт посылку одному, второй с другой точкой игнорируется);
  - не падать UNIQUE при дубле строки в одном файле;
  - конфликт (один id, разные точки/данные в одном файле) сообщать ошибкой.
"""
import os
import tempfile
import unittest

import openpyxl
from sqlalchemy import select, func

from src.database import DatabaseManager
from src.models import Client, DeliveryPoint
from src.client_import_service import ClientImportService, TEMPLATE_HEADERS


def _make_xlsx(rows):
    path = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(TEMPLATE_HEADERS)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


class ClientImportConflictsTest(unittest.TestCase):
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

    def _import(self, rows):
        path = _make_xlsx(rows)
        self._files.append(path)
        return self.svc.import_clients(path)

    def _count(self):
        return self.session.execute(
            select(func.count()).select_from(Client)
        ).scalar_one()

    def test_leading_zero_variants_same_point_one_client(self):
        res = self._import([
            ["0224933356", "Иванов", "", "Комсомольская 4"],
            ["224933356", "Иванов", "", "Комсомольская 4"],
        ])
        self.assertEqual(self._count(), 1, "один нормализованный id — один клиент")
        self.assertEqual(res.added, 1)
        self.assertEqual(len(res.errors), 0, "одинаковый дубль — без ошибки")

    def test_leading_zero_conflict_reported(self):
        res = self._import([
            ["0224933356", "Иванов", "", "Комсомольская 4"],
            ["224933356", "Иванов", "", "Кольцевая 16"],
        ])
        self.assertEqual(self._count(), 1)
        self.assertEqual(res.added, 1)
        self.assertEqual(len(res.errors), 1, "конфликт точек должен быть сообщён")
        c = self.session.execute(select(Client)).scalar_one()
        self.assertEqual(c.fixed_delivery_point, DeliveryPoint.KOMSOMOLSKAYA_4,
                         "побеждает первая строка")

    def test_zero_variant_updates_existing_db_client(self):
        self._import([["224933356", "Старое", "", "Комсомольская 4"]])
        res = self._import([["0224933356", "Новое", "+7", "Кольцевая 16"]])
        self.assertEqual(self._count(), 1, "не создаём второго клиента")
        self.assertEqual((res.added, res.updated), (0, 1))
        c = self.session.execute(select(Client)).scalar_one()
        self.assertEqual(c.full_name, "Новое")
        self.assertEqual(c.fixed_delivery_point, DeliveryPoint.KOLTSEVAYA_16)

    def test_exact_duplicate_row_no_crash(self):
        # Регресс: раньше дубль строки в одном файле падал UNIQUE на commit,
        # теряя весь импорт.
        res = self._import([
            ["224933356", "Иванов", "", "Комсомольская 4"],
            ["224933356", "Иванов", "", "Комсомольская 4"],
        ])
        self.assertEqual(self._count(), 1)
        self.assertEqual(res.added, 1)

    def test_stored_id_is_canonical(self):
        self._import([["0224933356", "Иванов", "", "Комсомольская 4"]])
        c = self.session.execute(select(Client)).scalar_one()
        self.assertEqual(c.ozon_client_id, "224933356",
                         "хранимый id канонизирован (без ведущих нулей)")


if __name__ == "__main__":
    unittest.main()
