"""Копия базы при запуске программы — в папку backups рядом с базой.

Копия снимается backup API SQLite (согласованный снимок, а не байты файла
посреди записи) во временный файл и появляется под настоящим именем только
после PRAGMA quick_check. Ротация идёт лишь после удачной копии: битая или
пустая копия не вытесняет целые.
"""
import contextlib
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional

from src.database import APP_DATA_DIR, DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

KEEP_BACKUPS = 30
_PREFIX = "ozon_sorter_"
_SUFFIX = ".db.bak"
_DATA_TABLES = ("clients", "import_sessions", "shipments")
_SQLITE_DONE = 101

# Сколько ждать, пока базу держит другой процесс (вторая копия программы посреди
# записи). backup API CPython при SQLITE_BUSY повторяет шаг бесконечно, а копия
# снимается до открытия окна — без предела программа не запустилась бы.
LOCK_TIMEOUT_S = 10.0


class BackupError(Exception):
    """Копия получилась, но не прошла проверку."""


def backup_database(db_path: str = DEFAULT_DB_PATH,
                    backup_dir: Optional[str] = None) -> Optional[str]:
    """Сделать копию базы. Возвращает путь копии; None — базы нет или копия не
    удалась (запуску программы это не мешает, причина — в журнале)."""
    if backup_dir is None:
        backup_dir = os.path.join(APP_DATA_DIR, "backups")
    if not os.path.exists(db_path):
        return None
    target = os.path.join(backup_dir, f"{_PREFIX}{datetime.now():%Y%m%d_%H%M%S}{_SUFFIX}")
    tmp = target + ".tmp"
    try:
        os.makedirs(backup_dir, exist_ok=True)
        if not _snapshot(db_path, tmp):
            logger.info("Database has no data, backup skipped")
            return None
        os.replace(tmp, target)
    except (sqlite3.Error, OSError, BackupError):
        logger.exception("Database backup failed, previous backups are kept")
        return None
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)
    _rotate(backup_dir)
    return target


def _snapshot(db_path: str, target: str) -> bool:
    """Снять проверенную копию в target. False — в базе нет данных."""
    deadline = time.monotonic() + LOCK_TIMEOUT_S

    def give_up_when_locked(status, remaining, total):
        if status != _SQLITE_DONE and time.monotonic() > deadline:
            raise BackupError("Database stays locked by another process")

    src = sqlite3.connect(db_path, timeout=min(1.0, LOCK_TIMEOUT_S))
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst, progress=give_up_when_locked)
            result = dst.execute("PRAGMA quick_check").fetchone()[0]
            has_data = result == "ok" and _has_data(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if result != "ok":
        raise BackupError(f"Backup copy failed quick_check: {result}")
    return has_data


def _has_data(conn: sqlite3.Connection) -> bool:
    """Есть клиенты, импорты или посылки — как _local_is_empty в sync_service."""
    for table in _DATA_TABLES:
        try:
            if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                return True
        except sqlite3.OperationalError:
            continue  # таблицы ещё нет
    return False


def _rotate(backup_dir: str) -> None:
    backups = sorted(name for name in os.listdir(backup_dir)
                     if name.startswith(_PREFIX) and name.endswith(_SUFFIX))
    for old in backups[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            logger.warning("Could not remove old backup %s", old, exc_info=True)
