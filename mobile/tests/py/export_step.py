"""Выгрузка кодом ПК: argv = база, точка, id сессии импорта, путь файла.
Печатает JSON {"export_id": n} или {"error": "..."}."""
import json
import sys

from src.database import DatabaseManager
from src.export_service import ExportService
from src.models import DeliveryPoint

db_path, point, session_id, out = sys.argv[1:5]
db = DatabaseManager(db_path=db_path)
session = db.get_session()
try:
    es = ExportService(session).generate_export(DeliveryPoint[point], out, int(session_id))
    result = {"export_id": es.id}
except Exception as e:
    session.rollback()
    result = {"error": str(e)}
finally:
    session.close()
    db.engine.dispose()
print(json.dumps(result, ensure_ascii=False))
