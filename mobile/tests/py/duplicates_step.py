"""Дубли кодом ПК. argv: база, действие (list | ship | dismiss | texts), id посылки.
Печатает JSON."""
import json
import sys

from src.database import DatabaseManager
from src.duplicate_service import (
    DUPLICATE_DAYS, INCOMPLETE_MIN, INCOMPLETE_PERCENT, DuplicateService,
    duplicates_alert_text, incomplete_report_text, is_incomplete,
)

db_path, action = sys.argv[1:3]
if action == "texts":
    print(json.dumps({
        "days": DUPLICATE_DAYS, "min": INCOMPLETE_MIN, "percent": INCOMPLETE_PERCENT,
        "alert": duplicates_alert_text(3), "incomplete": incomplete_report_text(7),
        "grid": [[is_incomplete(lost, kept) for kept in range(0, 200, 7)] for lost in range(0, 30)],
    }, ensure_ascii=False))
    sys.exit(0)

db = DatabaseManager(db_path=db_path)
session = db.get_session()
service = DuplicateService(session)
if action == "list":
    result = [{
        "shipment_id": r.shipment_id, "posting_number": r.posting_number,
        "product_label": r.product_label, "product_name": r.product_name,
        "client_name": r.client_name, "ozon_client_id": r.ozon_client_id, "phone": r.phone,
        "point": r.point.value if r.point else None, "point_label": r.point_label, "cell": r.cell,
        "delivered_label": r.delivered_label, "reappeared_label": r.reappeared_label,
    } for r in service.list_duplicates()]
else:
    result = getattr(service, action)(int(sys.argv[3]))
session.close()
db.engine.dispose()
print(json.dumps(result, ensure_ascii=False))
