"""«ПК» в перекрёстных тестах синхронизации: настоящий SyncService ПК.

argv: папка устройства, адрес WebDAV, команда, [аргумент].
Команды: init, add_client <id>, client_ids, check, push, pull, local_changed,
sha, reopen (открыть базу, как при запуске программы). Печатает JSON.
"""
import hashlib
import json
import os
import sys

from src.database import DatabaseManager
from src.models import Client, DeliveryPoint
from src.sync_service import SyncService
from src.webdav import WebDavClient

device_dir, url, cmd = sys.argv[1:4]
arg = sys.argv[4] if len(sys.argv) > 4 else None
db_path = os.path.join(device_dir, "ozon_sorter.db")
sync = SyncService(db_path, WebDavClient(url, "user", "secret", timeout=5), "OzonSorter",
                   os.path.join(device_dir, "sync_state.json"), "ПК", target_id="t1")


def reopen():
    db = DatabaseManager(db_path=db_path)
    db.create_tables()
    db.engine.dispose()


def meta_dict(meta):
    return None if meta is None else meta.__dict__


if cmd == "init":
    os.makedirs(device_dir, exist_ok=True)
    reopen()
    out = None
elif cmd == "reopen":
    reopen()
    out = None
elif cmd == "add_client":
    db = DatabaseManager(db_path=db_path)
    s = db.get_session()
    s.add(Client(ozon_client_id=arg, full_name="ПК", fixed_delivery_point=DeliveryPoint.KOLTSEVAYA_16))
    s.commit()
    s.close()
    db.engine.dispose()
    out = None
elif cmd == "client_ids":
    db = DatabaseManager(db_path=db_path)
    s = db.get_session()
    out = sorted(c.ozon_client_id for c in s.query(Client).all())
    s.close()
    db.engine.dispose()
elif cmd == "check":
    c = sync.check()
    out = {"status": c.status.value, "meta": meta_dict(c.meta), "remote_is_older": c.remote_is_older}
elif cmd == "push":
    out = sync.push(sync.check().meta)
elif cmd == "pull":
    out = sync.pull(sync.check().meta)
elif cmd == "local_changed":
    out = sync.local_changed()
elif cmd == "sha":
    with open(db_path, "rb") as f:
        out = hashlib.sha256(f.read()).hexdigest()
else:
    raise SystemExit(f"unknown command {cmd}")
print(json.dumps(out, ensure_ascii=False))
