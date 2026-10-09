"""«Скоро возврат» кодом ПК: argv = база. Печатает JSON {alert, text}."""
import json
import sys

from src.database import DatabaseManager
from src.stale_service import StaleShipmentService, return_alert_text

db = DatabaseManager(db_path=sys.argv[1])
session = db.get_session()
alert, overdue = StaleShipmentService(session).return_alert()
session.close()
db.engine.dispose()
print(json.dumps({"alert": [alert, overdue], "text": return_alert_text(alert, overdue),
                  "text_no_overdue": return_alert_text(alert, 0)}, ensure_ascii=False))
