"""Настройки синхронизации: сохранение, чтение, устойчивость к битому файлу."""
import json
import os
import stat
import sys
import tempfile
import unittest

from src.sync_config import (
    SyncConfig, load_config, save_config, DEFAULT_URL, DEFAULT_REMOTE_DIR,
)


class SyncConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ozon-cfg-")
        self.path = os.path.join(self.dir, "sync.json")

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def test_missing_file_gives_unconfigured_defaults(self):
        config = load_config(self.path)
        self.assertEqual(config.url, DEFAULT_URL)
        self.assertEqual(config.remote_dir, DEFAULT_REMOTE_DIR)
        self.assertFalse(config.is_configured)

    def test_roundtrip_keeps_all_fields(self):
        save_config(SyncConfig(
            url="https://dav.example.ru", login="ivan", password="пароль-приложения 123",
            remote_dir="Склад/База", device_name="Касса",
        ), self.path)
        config = load_config(self.path)
        self.assertEqual(config.url, "https://dav.example.ru")
        self.assertEqual(config.login, "ivan")
        self.assertEqual(config.password, "пароль-приложения 123")
        self.assertEqual(config.remote_dir, "Склад/База")
        self.assertEqual(config.device_name, "Касса")
        self.assertTrue(config.is_configured)

    def test_password_not_stored_as_plain_text(self):
        save_config(SyncConfig(login="ivan", password="qwerty-secret"), self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertNotIn("qwerty-secret", f.read())

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_file_readable_only_by_owner(self):
        save_config(SyncConfig(login="ivan", password="x"), self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_corrupted_file_gives_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertFalse(load_config(self.path).is_configured)

    def test_non_object_json_gives_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(["not", "a", "dict"], f)
        self.assertFalse(load_config(self.path).is_configured)

    @unittest.skipIf(sys.platform == "win32", "DPAPI is available on Windows")
    def test_password_from_another_machine_is_dropped_and_flagged(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"login": "ivan", "password": "AAAA", "password_protection": "dpapi"}, f)
        config = load_config(self.path)
        self.assertEqual(config.login, "ivan")
        self.assertEqual(config.password, "")
        self.assertFalse(config.is_configured)
        # Пользователю надо сказать, что синхронизация выключена не по его воле.
        self.assertTrue(config.password_unreadable)

    def test_readable_or_absent_password_is_not_flagged(self):
        save_config(SyncConfig(login="ivan", password="secret"), self.path)
        self.assertFalse(load_config(self.path).password_unreadable)
        self.assertFalse(load_config(os.path.join(self.dir, "missing.json")).password_unreadable)
        save_config(SyncConfig(login="ivan", password=""), self.path)
        self.assertFalse(load_config(self.path).password_unreadable)

    def test_empty_password_is_stored_as_empty_not_encrypted(self):
        # На Windows зашифрованная пустая строка непуста, и при чтении пароль
        # выглядел бы «сохранённым, но нечитаемым» — предупреждение при каждом запуске.
        save_config(SyncConfig(login="ivan", password=""), self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["password"], "")
        self.assertEqual(data["password_protection"], "none")

    def test_target_id_depends_on_server_account_and_folder(self):
        base = SyncConfig(url="https://a.example", login="ivan", remote_dir="Склад")
        same = SyncConfig(url="https://a.example", login="ivan", remote_dir="/Склад/")
        self.assertEqual(base.target_id, same.target_id)
        for other in (
            SyncConfig(url="https://b.example", login="ivan", remote_dir="Склад"),
            SyncConfig(url="https://a.example", login="petr", remote_dir="Склад"),
            SyncConfig(url="https://a.example", login="ivan", remote_dir="Другая"),
        ):
            self.assertNotEqual(base.target_id, other.target_id)

    def test_target_id_does_not_contain_password(self):
        config = SyncConfig(login="ivan", password="qwerty-secret")
        self.assertNotIn("qwerty", config.target_id)
        self.assertEqual(config.target_id, SyncConfig(login="ivan", password="other").target_id)

    def test_empty_password_is_not_configured(self):
        save_config(SyncConfig(login="ivan", password=""), self.path)
        self.assertFalse(load_config(self.path).is_configured)


if __name__ == "__main__":
    unittest.main()
