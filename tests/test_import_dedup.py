"""Регресс: повторная встреча posting_number не должна падать UNIQUE constraint.

Баг: проверка «посылка уже в БД» выполнялась только для matched-клиента.
Для NOT_OURS и KTY код сразу делал INSERT → при повторном импорте того же
отправления (или дубле строки в одном файле) sqlite3.IntegrityError на
UNIQUE shipments.posting_number.
"""
import os
import tempfile
import unittest

import openpyxl
from sqlalchemy import select, func

from src.database import DatabaseManager
from src.models import Client, Shipment, DeliveryPoint, AssignmentStatus
from src.services import ImportService


def _make_report(postings):
    path = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Этикетка\nНазвание", "Номер отправления", "Тип", "Статус", "Ячейка"])
    for p in postings:
        ws.append([f"LBL\n{p}", p, "Обычный", "Готов", "A-01"])
    wb.save(path)
    return path


class ImportDedupTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseManager(db_path=self.db_path)
        self.db.create_tables()
        self.session = self.db.get_session()
        # Заведён только клиент 111; 888888 — чужой (NOT_OURS).
        self.session.add(Client(
            ozon_client_id="111", full_name="Тест",
            fixed_delivery_point=DeliveryPoint.KOMSOMOLSKAYA_4,
        ))
        self.session.commit()
        self.importer = ImportService(self.session)
        self._files = []

    def tearDown(self):
        self.session.close()
        os.remove(self.db_path)
        for f in self._files:
            if os.path.exists(f):
                os.remove(f)

    def _import(self, postings):
        path = _make_report(postings)
        self._files.append(path)
        return self.importer.process_import(path)

    def _count(self, posting):
        return self.session.execute(
            select(func.count()).select_from(Shipment)
            .where(Shipment.posting_number == posting)
        ).scalar_one()

    def test_reimport_not_ours_does_not_raise(self):
        posting = "888888-0119-1"
        s1 = self._import([posting])
        # Повторный импорт того же чужого отправления не должен падать UNIQUE.
        s2 = self._import([posting])
        self.assertEqual(self._count(posting), 1, "дубликата записи быть не должно")
        ship = self.session.execute(
            select(Shipment).where(Shipment.posting_number == posting)
        ).scalar_one()
        self.assertEqual(ship.assignment_status, AssignmentStatus.EXCLUDED_NOT_OURS)
        self.assertEqual(ship.import_session_id, s1.id, "первая встреча не меняется")
        self.assertEqual(ship.last_seen_import_session_id, s2.id, "last_seen едет вперёд")

    def test_duplicate_rows_in_single_file(self):
        posting = "888888-0119-1"
        # Тот же posting дважды в одном файле — частый случай в отчётах Ozon.
        self._import([posting, posting])
        self.assertEqual(self._count(posting), 1)


if __name__ == "__main__":
    unittest.main()
