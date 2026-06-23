"""ShipmentControlService: фильтры списка, ручная смена статуса, сброс по точке."""
import os
import tempfile
import unittest
from datetime import datetime

from src.database import DatabaseManager
from src.models import (
    Client, Shipment, ImportSession, AssignmentStatus, DeliveryPoint,
)
from src.shipment_control_service import ShipmentControlService


class ShipmentControlTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseManager(db_path=self.db_path)
        self.db.create_tables()
        self.s = self.db.get_session()
        self.svc = ShipmentControlService(self.s)

        self.client = Client(ozon_client_id="224933356", full_name="Литвинова",
                             fixed_delivery_point=DeliveryPoint.KOMSOMOLSKAYA_4)
        self.s.add(self.client)
        self.imp = ImportSession(source_file_name="x", source_file_sha256="h",
                                started_at=datetime.now())
        self.s.add(self.imp)
        self.s.flush()

        self._ship("0224933356-1-1", DeliveryPoint.KOMSOMOLSKAYA_4, AssignmentStatus.TO_SHIP)
        self._ship("0224933356-2-1", DeliveryPoint.KOMSOMOLSKAYA_4, AssignmentStatus.ON_POINT)
        self._ship("0224933356-3-1", DeliveryPoint.KOLTSEVAYA_16, AssignmentStatus.TO_SHIP)
        # не наша — не должна попадать в контроль
        self._ship("999-9-9", DeliveryPoint.KOLTSEVAYA_16, AssignmentStatus.EXCLUDED_NOT_OURS,
                   client_id=None)
        self.s.commit()

    def tearDown(self):
        self.s.close()
        os.remove(self.db_path)

    def _ship(self, posting, point, status, client_id="__own__"):
        cid = self.client.id if client_id == "__own__" else client_id
        self.s.add(Shipment(
            posting_number=posting, client_id=cid,
            ozon_client_id_raw=posting.split("-")[0],
            product_name="Товар", cell="A-1",
            assignment_status=status, assigned_point=point,
            import_session_id=self.imp.id, last_seen_import_session_id=self.imp.id,
        ))

    def test_list_only_ours(self):
        rows = self.svc.list_shipments()
        self.assertEqual(len(rows), 3)  # без EXCLUDED_NOT_OURS
        self.assertTrue(all(r.client_id is not None for r in rows))

    def test_filter_by_point_and_status(self):
        self.assertEqual(len(self.svc.list_shipments(point=DeliveryPoint.KOMSOMOLSKAYA_4)), 2)
        self.assertEqual(len(self.svc.list_shipments(status=AssignmentStatus.ON_POINT)), 1)

    def test_search_by_posting_and_ozon_id_with_leading_zero(self):
        # поиск по части номера
        self.assertEqual(len(self.svc.list_shipments(search="2-1")), 1)
        # поиск по Ozon ID без нуля находит посылки с нулём в номере
        rows = self.svc.list_shipments(search="224933356")
        self.assertEqual(len(rows), 3)

    def test_set_status_sets_timestamps(self):
        ship = self.svc.list_shipments(status=AssignmentStatus.TO_SHIP)[0]
        self.svc.set_status(ship.id, AssignmentStatus.DELIVERED)
        self.s.refresh(ship)
        self.assertEqual(ship.assignment_status, AssignmentStatus.DELIVERED)
        self.assertIsNotNone(ship.delivered_at)
        self.assertIsNotNone(ship.shipped_to_point_at)
        # возврат в работу очищает отметки
        self.svc.set_status(ship.id, AssignmentStatus.TO_SHIP)
        self.s.refresh(ship)
        self.assertIsNone(ship.delivered_at)
        self.assertIsNone(ship.shipped_to_point_at)

    def test_reset_point_deletes_only_that_point(self):
        self.assertEqual(self.svc.count_for_point(DeliveryPoint.KOMSOMOLSKAYA_4), 2)
        deleted = self.svc.reset_point(DeliveryPoint.KOMSOMOLSKAYA_4)
        self.assertEqual(deleted, 2)
        self.assertEqual(self.svc.count_for_point(DeliveryPoint.KOMSOMOLSKAYA_4), 0)
        # Кольцевая не тронута (1 наша + 1 не наша)
        self.assertEqual(self.svc.count_for_point(DeliveryPoint.KOLTSEVAYA_16), 2)


if __name__ == "__main__":
    unittest.main()
