"""Миграция старой БД: колонка delivery_point_policy физически удаляется,
данные клиентов и ссылки посылок сохраняются, конфликтов нет.
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import select

from src.database import DatabaseManager
from src.models import Client, Shipment, ImportSession, AssignmentStatus, DeliveryPoint


OLD_CLIENTS_DDL = """
CREATE TABLE clients (
    id INTEGER NOT NULL PRIMARY KEY,
    ozon_client_id VARCHAR NOT NULL UNIQUE,
    full_name VARCHAR,
    phone VARCHAR,
    delivery_point_policy VARCHAR NOT NULL,
    fixed_delivery_point VARCHAR,
    notes TEXT,
    is_active BOOLEAN,
    created_at DATETIME,
    updated_at DATETIME,
    CHECK (
        (delivery_point_policy = 'FIXED' AND fixed_delivery_point IS NOT NULL) OR
        (delivery_point_policy = 'MANUAL' AND fixed_delivery_point IS NULL)
    )
)
"""


def _clients_columns(db_path):
    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(clients)")]
    con.close()
    return cols


class MigrationDropPolicyTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        # Старая схема clients (с политикой и CHECK-констрейнтом) + данные.
        con = sqlite3.connect(self.db_path)
        con.execute(OLD_CLIENTS_DDL)
        con.execute(
            "INSERT INTO clients (id, ozon_client_id, full_name, phone, "
            "delivery_point_policy, fixed_delivery_point, is_active) "
            "VALUES (1,'224933356','Литвинова',NULL,'FIXED','KOMSOMOLSKAYA_4',1)"
        )
        con.execute(
            "INSERT INTO clients (id, ozon_client_id, full_name, phone, "
            "delivery_point_policy, fixed_delivery_point, is_active) "
            "VALUES (2,'999','Мануальный',NULL,'MANUAL',NULL,1)"
        )
        con.commit()
        con.close()

    def tearDown(self):
        os.remove(self.db_path)

    def test_policy_column_dropped_and_data_preserved(self):
        self.assertIn("delivery_point_policy", _clients_columns(self.db_path))

        db = DatabaseManager(db_path=self.db_path)
        db.create_tables()  # запускает миграцию

        cols = _clients_columns(self.db_path)
        self.assertNotIn("delivery_point_policy", cols, "колонка должна исчезнуть")
        self.assertIn("fixed_delivery_point", cols)

        s = db.get_session()
        clients = s.execute(select(Client).order_by(Client.id)).scalars().all()
        self.assertEqual(len(clients), 2)
        self.assertEqual(clients[0].ozon_client_id, "224933356")
        self.assertEqual(clients[0].fixed_delivery_point, DeliveryPoint.KOMSOMOLSKAYA_4)
        self.assertIsNone(clients[1].fixed_delivery_point)  # бывший MANUAL

        # таблица рабочая: посылка, ссылающаяся на клиента, создаётся и читается
        imp = ImportSession(source_file_name="x", source_file_sha256="h",
                            started_at=datetime.now())
        s.add(imp); s.flush()
        s.add(Shipment(
            posting_number="224933356-1-1", client_id=clients[0].id,
            ozon_client_id_raw="0224933356", assignment_status=AssignmentStatus.TO_SHIP,
            assigned_point=DeliveryPoint.KOMSOMOLSKAYA_4, import_session_id=imp.id,
            last_seen_import_session_id=imp.id,
        ))
        s.commit()
        ship = s.execute(select(Shipment)).scalar_one()
        self.assertEqual(ship.client.full_name, "Литвинова")
        s.close()

    def test_migration_idempotent(self):
        DatabaseManager(db_path=self.db_path).create_tables()
        # повторный запуск не падает и колонку не возвращает
        DatabaseManager(db_path=self.db_path).create_tables()
        self.assertNotIn("delivery_point_policy", _clients_columns(self.db_path))


if __name__ == "__main__":
    unittest.main()
