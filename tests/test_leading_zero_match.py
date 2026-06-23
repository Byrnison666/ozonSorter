"""Матчинг клиента устойчив к ведущим нулям в Ozon ID.

Отчёт Ozon дополняет id нулём слева (0224933356), а клиент в базе заведён без
него (224933356) — особенно после импорта из Excel. Сравнение по числовому
значению должно их совмещать.
"""
import os
import tempfile
import unittest

import openpyxl
from sqlalchemy import select

from src.database import DatabaseManager
from src.models import Client, Shipment, AssignmentStatus, DeliveryPoint, DeliveryPointPolicy
from src.services import ImportService
from src.parser import ExcelParser


def _report(postings):
    path = tempfile.mktemp(suffix=".xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Этикетка\nНазвание", "Номер отправления", "Ячейка"])
    for p in postings:
        ws.append([f"LBL\n{p}", p, "44-1"])
    wb.save(path)
    return path


class LeadingZeroMatchTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = DatabaseManager(db_path=self.db_path)
        self.db.create_tables()
        self.session = self.db.get_session()
        self._files = []

    def tearDown(self):
        self.session.close()
        os.remove(self.db_path)
        for f in self._files:
            if os.path.exists(f):
                os.remove(f)

    def _add_client(self, ozon_id):
        self.session.add(Client(
            ozon_client_id=ozon_id,
            delivery_point_policy=DeliveryPointPolicy.FIXED,
            fixed_delivery_point=DeliveryPoint.KOMSOMOLSKAYA_4,
        ))
        self.session.commit()

    def _import(self, postings):
        path = _report(postings)
        self._files.append(path)
        return ImportService(self.session).process_import(path)

    def test_normalize_ozon_id(self):
        self.assertEqual(ExcelParser.normalize_ozon_id("0224933356"), "224933356")
        self.assertEqual(ExcelParser.normalize_ozon_id("224933356"), "224933356")
        self.assertEqual(ExcelParser.normalize_ozon_id("45260199"), "45260199")
        self.assertEqual(ExcelParser.normalize_ozon_id("000"), "0")

    def test_client_without_zero_matches_report_with_zero(self):
        self._add_client("224933356")  # в базе без ведущего нуля
        sess = self._import(["0224933356-0289-1", "0224933356-0281-1"])  # в отчёте с нулём
        self.assertEqual(sess.matched_rows, 2)
        ships = self.session.execute(
            select(Shipment).where(Shipment.client_id.isnot(None))
        ).scalars().all()
        self.assertEqual(len(ships), 2)
        self.assertTrue(all(s.assignment_status == AssignmentStatus.TO_SHIP for s in ships))
        self.assertTrue(all(s.assigned_point == DeliveryPoint.KOMSOMOLSKAYA_4 for s in ships))

    def test_client_with_zero_matches_report_without_zero(self):
        self._add_client("0247354782")  # в базе с нулём
        sess = self._import(["247354782-0001-1"])  # в отчёте без нуля (8-9 знаков)
        self.assertEqual(sess.matched_rows, 1)

    def test_different_numbers_do_not_match(self):
        self._add_client("224933356")
        sess = self._import(["224933357-0001-1"])  # отличается последней цифрой
        self.assertEqual(sess.matched_rows, 0)
        self.assertEqual(sess.not_ours_rows, 1)


if __name__ == "__main__":
    unittest.main()
