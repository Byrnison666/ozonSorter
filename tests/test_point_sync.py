"""При импорте точка ещё не привезённой посылки синхронизируется с актуальной
точкой клиента. Иначе смена точки клиента не доезжает до pending-посылок и они
уходят на старую точку (конфликт маршрутизации)."""
import os
import tempfile
import unittest

import openpyxl
from sqlalchemy import select

from src.database import DatabaseManager
from src.models import Client, Shipment, AssignmentStatus, DeliveryPoint
from src.services import ImportService


def _report(postings):
    path = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Этикетка\nНазвание", "Номер отправления", "Статус", "Ячейка"])
    for p in postings:
        ws.append([f"L\n{p}", p, "Готово к выдаче", "A-1"])
    wb.save(path)
    return path


class PointSyncTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseManager(db_path=self.db_path)
        self.db.create_tables()
        self.session = self.db.get_session()
        self.client = Client(
            ozon_client_id="111", full_name="A",
            fixed_delivery_point=DeliveryPoint.KOMSOMOLSKAYA_4,
        )
        self.session.add(self.client)
        self.session.commit()
        self.imp = ImportService(self.session)
        self._files = []

    def tearDown(self):
        self.session.close()
        os.remove(self.db_path)
        for f in self._files:
            if os.path.exists(f):
                os.remove(f)

    def _import(self, postings):
        path = _report(postings)
        self._files.append(path)
        return self.imp.process_import(path)

    def test_pending_parcel_follows_client_point_change(self):
        self._import(["111-0001-1"])
        sh = self.session.execute(select(Shipment)).scalar_one()
        self.assertEqual(sh.assigned_point, DeliveryPoint.KOMSOMOLSKAYA_4)
        self.assertEqual(sh.assignment_status, AssignmentStatus.TO_SHIP)

        # Клиента перевели на другую точку.
        self.client.fixed_delivery_point = DeliveryPoint.KOLTSEVAYA_16
        self.session.commit()

        self._import(["111-0001-1"])
        self.session.refresh(sh)
        self.assertEqual(sh.assigned_point, DeliveryPoint.KOLTSEVAYA_16,
                         "pending-посылка должна переехать на новую точку клиента")


if __name__ == "__main__":
    unittest.main()
