"""Миграция-дедуп клиентов по нормализованному Ozon ID.

Боевые базы до v1.6 могли накопить два клиента на один id («0224933356» и
«224933356»). v1.6 канонизирует id при импорте → UPDATE натыкается на UNIQUE и
весь импорт падает (sqlite3.IntegrityError). Миграция при старте обязана свести
дубли в одного: посылки перецепить, лишних удалить, id канонизировать.
"""
import os
import tempfile
import unittest

import openpyxl
from sqlalchemy import select, func

from src.database import DatabaseManager
from src.models import (
    Client, Shipment, ImportSession, DeliveryPoint, AssignmentStatus,
)
from src.client_import_service import ClientImportService, TEMPLATE_HEADERS


class ClientDedupMigrationTest(unittest.TestCase):
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

    def _seed_duplicates(self):
        """Два клиента-дубля (с нулём и без) + посылка на каждого — как в боевой
        базе до канонизации. Создаём ORM-объекты напрямую, минуя сервис импорта
        (он бы канонизировал id и дублей бы не было)."""
        c1 = Client(ozon_client_id="0224933356", full_name="Литвинова",
                    fixed_delivery_point=DeliveryPoint.KOMSOMOLSKAYA_4)
        c2 = Client(ozon_client_id="224933356", full_name="Литвинова дубль",
                    fixed_delivery_point=DeliveryPoint.KOLTSEVAYA_16)
        imp = ImportSession(source_file_name="x", source_file_sha256="h")
        self.session.add_all([c1, c2, imp])
        self.session.flush()
        for posting, client in (("224933356-0001-1", c1), ("224933356-0002-1", c2)):
            self.session.add(Shipment(
                posting_number=posting, client_id=client.id,
                ozon_client_id_raw="224933356",
                assignment_status=AssignmentStatus.TO_SHIP,
                import_session_id=imp.id, last_seen_import_session_id=imp.id,
            ))
        self.session.commit()
        self._dup_ids = (c1.id, c2.id)

    def test_migration_merges_duplicates(self):
        self._seed_duplicates()
        self.db._migrate()  # идемпотентно, как при следующем старте

        clients = self.session.execute(select(Client)).scalars().all()
        self.assertEqual(len(clients), 1, "дубли сведены в одного")
        self.assertEqual(clients[0].ozon_client_id, "224933356", "id канонизирован")
        survivor_id = clients[0].id

        # Посылки обоих дублей перецеплены на выжившего — ничего не потеряно.
        cnt = self.session.execute(
            select(func.count()).select_from(Shipment)
            .where(Shipment.client_id == survivor_id)
        ).scalar_one()
        self.assertEqual(cnt, 2)
        orphans = self.session.execute(
            select(func.count()).select_from(Shipment)
            .where(Shipment.client_id.notin_([survivor_id]))
        ).scalar_one()
        self.assertEqual(orphans, 0, "висячих ссылок нет")

    def test_idempotent(self):
        self._seed_duplicates()
        self.db._migrate()
        self.db._migrate()  # повторный старт не должен ничего ломать
        self.assertEqual(
            self.session.execute(select(func.count()).select_from(Client)).scalar_one(),
            1,
        )

    def test_import_after_migration_no_crash(self):
        self._seed_duplicates()
        self.db._migrate()
        # Импорт того же клиента (с нулём) больше не падает UNIQUE.
        path = tempfile.mktemp(suffix=".xlsx")
        self._files.append(path)
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(TEMPLATE_HEADERS)
        ws.append(["0224933356", "Литвинова Елена", "", "Комсомольская 4"])
        wb.save(path)
        res = ClientImportService(self.session).import_clients(path)
        self.assertEqual((res.added, res.updated, len(res.errors)), (0, 1, 0))


if __name__ == "__main__":
    unittest.main()
