"""База эталона выгрузки и залежавшихся в argv[1]/base.db: два отчёта,
посылки с ячейками для натуральной сортировки, повреждения, отметки выгрузки,
разные даты первой встречи."""
import os
import sys
from datetime import datetime

from src.database import DatabaseManager
from src.models import (
    AssignmentStatus as S, Client, DeliveryPoint as P, ImportSession, Shipment,
)

OUT = sys.argv[1]
STAMP = datetime(2026, 9, 1, 10, 0, 0)

db = DatabaseManager(db_path=os.path.join(OUT, "base.db"))
db.create_tables()
s = db.get_session()

anna = Client(ozon_client_id="224933356", full_name="Анна", phone="+7 900",
              fixed_delivery_point=P.KOMSOMOLSKAYA_4, created_at=STAMP, updated_at=STAMP)
boris = Client(ozon_client_id="147012251", full_name=None, phone=None,
               fixed_delivery_point=P.KOLTSEVAYA_16, created_at=STAMP, updated_at=STAMP)
gone = Client(ozon_client_id="555000111", full_name="Удалён", is_active=False,
              fixed_delivery_point=P.KOMSOMOLSKAYA_4, created_at=STAMP, updated_at=STAMP)
s.add_all([anna, boris, gone])
s.flush()

s1 = ImportSession(source_file_name="r1.xlsx", source_file_sha256="1" * 64,
                   started_at=datetime(2026, 9, 20, 9, 0), finished_at=datetime(2026, 9, 20, 9, 1))
s2 = ImportSession(source_file_name="r2.xlsx", source_file_sha256="2" * 64,
                   started_at=datetime(2026, 9, 29, 23, 59), finished_at=datetime(2026, 9, 30, 0, 1))
s.add_all([s1, s2])
s.flush()

n = 0


def ship(client, cell, *, status=S.TO_SHIP, point=None, seen=None, exported=None,
         name="Товар", label="lbl", damaged=False, first=datetime(2026, 9, 29, 8, 0)):
    global n
    n += 1
    s.add(Shipment(
        posting_number=f"{client.ozon_client_id if client else 'ii'}-{n:04d}-1",
        client_id=client.id if client else None,
        ozon_client_id_raw=client.ozon_client_id if client else "",
        product_label=label, product_name=name, cell=cell, is_damaged=damaged,
        is_kty=client is None, assignment_status=status,
        assigned_point=point if point is not None else (client.fixed_delivery_point if client else None),
        import_session_id=s1.id, last_seen_import_session_id=(seen or s2).id,
        exported_import_session_id=exported.id if exported else None,
        first_seen_at=first, last_seen_at=first,
    ))


for cell in ["A-10", "A-2", "a-2", "10-1", "2-1", None, "", "Б-1", "1-1", "A-2-3", "A-02"]:
    ship(anna, cell, first=datetime(2026, 9, 29 - (n % 9), 8, 0))
ship(anna, "3-1", damaged=True, name="Ваза", first=datetime(2026, 9, 22, 23, 59))
ship(anna, "3-2", name=None, label="только-этикетка")
ship(anna, "3-3", name="", label="", first=datetime(2026, 9, 1, 0, 0))
ship(anna, "3-4", name="=SUM(A1)", label="_x0041_")
ship(anna, "4-1", exported=s1)                       # выгружена в прошлой сессии
ship(anna, "4-2", exported=s2)                       # в этой же — повторно попадает
ship(anna, "4-3", status=S.RETURNED, first=datetime(2026, 9, 10, 12, 0))
ship(anna, "4-4", status=S.ON_POINT)
ship(anna, "5-1", seen=s1)                           # только в старом отчёте
ship(anna, "5-2", seen=s1, damaged=True)
ship(boris, "7-1", name="Кольцевая 1")
ship(boris, "7-2", damaged=True, first=datetime(2026, 9, 15, 0, 0))
ship(gone, "8-1")                                    # клиент удалён
ship(None, "9-1", status=S.EXCLUDED_KTY)
s.commit()
s.close()
db.engine.dispose()
