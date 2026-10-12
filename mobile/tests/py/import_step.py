"""Один импорт отчёта кодом ПК: argv[1] — база, argv[2] — xlsx.
Печатает JSON {"session_id": n, "skipped_duplicates": k} или {"error": "..."}."""
import json
import sys

from src.database import DatabaseManager
from src.services import ImportService

db = DatabaseManager(db_path=sys.argv[1])
session = db.get_session()
try:
    service = ImportService(session)
    result = {"session_id": service.process_import(sys.argv[2]).id,
              "skipped_duplicates": service.skipped_duplicates}
except Exception as e:  # эталону важен сам факт отказа
    session.rollback()
    result = {"error": f"{type(e).__name__}: {e}"}
finally:
    session.close()
    db.engine.dispose()
print(json.dumps(result, ensure_ascii=False))
