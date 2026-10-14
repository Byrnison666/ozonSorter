"""Копия базы при запуске программы (src/backup.py): согласованный снимок через
backup API SQLite, проверка перед публикацией, ротация только после удачной
копии — битая база не вытесняет целые копии.
"""
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from src import backup

PAGE = 4096


def _make_db(path, rows=500):
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA page_size = {PAGE}")
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO clients (name) VALUES (?)",
                     [(f"client-{i}-" + "x" * 200,) for i in range(rows)])
    conn.commit()
    conn.close()


def _count(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT count(*) FROM clients").fetchone()[0]
    finally:
        conn.close()


class BackupDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ozon-backup-")
        self.db = os.path.join(self.dir, "ozon_sorter.db")
        self.backups = os.path.join(self.dir, "backups")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _listing(self):
        return sorted(os.listdir(self.backups)) if os.path.isdir(self.backups) else []

    def _old_copies(self, count):
        """count целых копий прошлых запусков (старше любой новой)."""
        os.makedirs(self.backups, exist_ok=True)
        names = [f"ozon_sorter_2020{1 + i // 28:02d}{1 + i % 28:02d}_120000.db.bak"
                 for i in range(count)]
        for name in names:
            _make_db(os.path.join(self.backups, name), rows=1)
        return names

    def test_copy_is_consistent_database(self):
        _make_db(self.db)
        path = backup.backup_database(self.db, self.backups)
        self.assertIsNotNone(path)
        self.assertEqual(self._listing(), [os.path.basename(path)])   # без временных файлов
        self.assertRegex(os.path.basename(path), r"^ozon_sorter_\d{8}_\d{6}\.db\.bak$")
        self.assertEqual(_count(path), 500)

    def test_missing_database_makes_no_copy(self):
        self.assertIsNone(backup.backup_database(self.db, self.backups))
        self.assertEqual(self._listing(), [])

    def test_keeps_last_30_and_ignores_other_files(self):
        old = self._old_copies(32)
        os.makedirs(os.path.join(self.backups, "presync"))
        with open(os.path.join(self.backups, "notes.txt"), "w") as f:
            f.write("not a backup")
        _make_db(self.db)
        path = backup.backup_database(self.db, self.backups)
        copies = [n for n in self._listing() if n.endswith(".db.bak")]
        self.assertEqual(len(copies), backup.KEEP_BACKUPS)
        self.assertEqual(copies, sorted(old[3:] + [os.path.basename(path)]))
        self.assertIn("presync", self._listing())
        self.assertIn("notes.txt", self._listing())

    def test_file_that_is_not_a_database_does_not_push_out_copies(self):
        old = self._old_copies(backup.KEEP_BACKUPS)
        with open(self.db, "wb") as f:
            f.write(b"<html>not a database</html>" * 200)
        with self.assertLogs("src.backup", "ERROR"):
            self.assertIsNone(backup.backup_database(self.db, self.backups))
        self.assertEqual(self._listing(), old)

    def test_corrupted_database_fails_check_and_keeps_copies(self):
        old = self._old_copies(backup.KEEP_BACKUPS)
        _make_db(self.db)
        with open(self.db, "r+b") as f:     # испорченная страница таблицы
            f.seek(2 * PAGE + 16)
            f.write(b"\xff" * (PAGE - 32))
        with self.assertLogs("src.backup", "ERROR"):
            self.assertIsNone(backup.backup_database(self.db, self.backups))
        self.assertEqual(self._listing(), old)
        for name in old:
            self.assertEqual(_count(os.path.join(self.backups, name)), 1)

    def test_empty_database_does_not_push_out_copies(self):
        old = self._old_copies(backup.KEEP_BACKUPS)
        conn = sqlite3.connect(self.db)     # схема есть, данных нет
        for table in ("clients", "import_sessions", "shipments"):
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        self.assertIsNone(backup.backup_database(self.db, self.backups))
        self.assertEqual(self._listing(), old)
        open(self.db, "wb").close()         # файл нулевой длины
        self.assertIsNone(backup.backup_database(self.db, self.backups))
        self.assertEqual(self._listing(), old)

    def test_locked_database_does_not_hang_startup(self):
        old = self._old_copies(2)
        _make_db(self.db)
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")   # другой процесс посреди записи
        result = []
        worker = threading.Thread(
            target=lambda: result.append(backup.backup_database(self.db, self.backups)),
            daemon=True)
        try:
            with mock.patch.object(backup, "LOCK_TIMEOUT_S", 0.5), \
                    self.assertLogs("src.backup", "ERROR"):
                worker.start()
                worker.join(10)
            self.assertFalse(worker.is_alive(), "backup waits for the lock forever")
        finally:
            holder.rollback()
            holder.close()
            worker.join(10)
        self.assertEqual(result, [None])
        self.assertEqual(self._listing(), old)


if __name__ == "__main__":
    unittest.main()
