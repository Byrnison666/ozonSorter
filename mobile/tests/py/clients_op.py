"""Одна операция с клиентами кодом ПК: argv[1] — база, argv[2] — JSON операции.

add/edit/delete выполняются настоящим экраном «Клиенты» (ClientsScreen) с
подменёнными диалогами; import — ClientImportService. Печатает JSON
{"messages": [...]} (предупреждения и ошибки, которые увидел бы оператор)
и для import — {"result": {...}}.
"""
import json
import sys
from unittest import mock

from PySide2.QtWidgets import QApplication, QDialog, QMessageBox

from src.client_import_service import ClientImportService
from src.database import DatabaseManager
from src.models import Client
from src.ui import clients_screen

op = json.loads(sys.argv[2])
db = DatabaseManager(db_path=sys.argv[1])
messages = []
out = {}

if op["op"] == "import":
    session = db.get_session()
    try:
        r = ClientImportService(session).import_clients(op["file"])
        out["result"] = {"added": r.added, "updated": r.updated, "data_rows": r.data_rows,
                         "errors": [[row, msg] for row, msg in r.errors]}
    except ValueError as e:
        session.rollback()
        messages.append(str(e))
    finally:
        session.close()
else:
    app = QApplication.instance() or QApplication([])

    class FakeDialog:
        def __init__(self, parent=None, initial=None):
            pass

        def exec_(self):
            return QDialog.Accepted

        def get_data(self):
            return dict(op["data"])

    def record(_parent, _title, text):
        messages.append(text)

    target_id = None
    if "target" in op:
        s = db.get_session()
        target_id = s.query(Client.id).filter(Client.ozon_client_id == op["target"]).scalar()
        s.close()

    screen = clients_screen.ClientsScreen(db, mock.Mock())
    with mock.patch.object(clients_screen, "ClientDialog", FakeDialog), \
            mock.patch.object(clients_screen, "ask_yes_no", return_value=True), \
            mock.patch.object(QMessageBox, "warning", side_effect=record), \
            mock.patch.object(QMessageBox, "critical", side_effect=record), \
            mock.patch.object(QMessageBox, "information", side_effect=record), \
            mock.patch.object(screen, "_selected_client_id", return_value=target_id):
        {"add": screen.on_add_client, "edit": screen.on_edit_client,
         "delete": screen.on_delete_client}[op["op"]]()

db.engine.dispose()
out["messages"] = messages
print(json.dumps(out, ensure_ascii=False))
