"""Залежавшиеся кодом ПК: argv = база, точка ('' — все), min_days, путь xlsx.
Печатает JSON-список строк и пишет выгрузку."""
import json
import sys

from src.database import DatabaseManager
from src.models import DeliveryPoint
from src.stale_service import StaleShipmentService

db_path, point, min_days, out = sys.argv[1:5]
db = DatabaseManager(db_path=db_path)
session = db.get_session()
service = StaleShipmentService(session)
rows = service.list_stale(DeliveryPoint[point] if point else None, int(min_days))
service.export_xlsx(rows, out)
session.close()
db.engine.dispose()
print(json.dumps([{
    "days": r.days, "cell": r.cell, "posting_number": r.posting_number,
    "product_label": r.product_label, "product_name": r.product_name,
    "client_name": r.client_name, "ozon_client_id": r.ozon_client_id, "phone": r.phone,
    "point": r.point.value if r.point else None, "first_seen_label": r.first_seen_label,
    "is_returned": r.is_returned, "cells": r.as_cells(),
} for r in rows], ensure_ascii=False))
