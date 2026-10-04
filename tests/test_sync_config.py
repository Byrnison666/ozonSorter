"""Настройки синхронизации: сохранение, чтение, устойчивость к битому файлу,
переход с настроек WebDAV-версии."""
import json
import os
import stat
import sys
import tempfile
import unittest

from src.sync_config import (
    SyncConfig, adopt_legacy_state, forget_legacy, load_config, save_config,
    _legacy_target_id,
)


def _config(**kwargs):
    fields: dict = dict(token="tok-123", refresh_token="ref-456", uid="1001", login="ivan",
                        device_name="Касса")
    fields.update(kwargs)
    return SyncConfig(**fields)


class SyncConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ozon-cfg-")
        self.path = os.path.join(self.dir, "sync.json")
        self.state_path = os.path.join(self.dir, "sync_state.json")

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def _write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_missing_file_gives_unconfigured_defaults(self):
        config = load_config(self.path)
        self.assertFalse(config.is_configured)
        self.assertFalse(config.token_unreadable)
        self.assertTrue(config.device_name)

    def test_roundtrip_keeps_all_fields(self):
        save_config(_config(token_expires_at=1900000000), self.path)
        config = load_config(self.path)
        self.assertEqual((config.token, config.refresh_token, config.uid, config.login,
                          config.device_name, config.token_expires_at),
                         ("tok-123", "ref-456", "1001", "ivan", "Касса", 1900000000))
        self.assertTrue(config.is_configured)

    def test_tokens_not_stored_as_plain_text(self):
        save_config(_config(token="qwerty-access", refresh_token="qwerty-refresh"), self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("qwerty-access", text)
        self.assertNotIn("qwerty-refresh", text)

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_file_readable_only_by_owner(self):
        save_config(_config(), self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_api_url_and_folder_are_not_saved(self):
        save_config(_config(api_url="http://127.0.0.1:1/v1/disk", remote_dir="Тест"), self.path)
        data = self._read()
        self.assertFalse({"api_url", "remote_dir"} & set(data))

    def test_corrupted_file_gives_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertFalse(load_config(self.path).is_configured)

    def test_non_object_json_gives_defaults(self):
        self._write(["not", "a", "dict"])
        self.assertFalse(load_config(self.path).is_configured)

    def test_bad_expiry_type_is_treated_as_unknown(self):
        save_config(_config(), self.path)
        data = self._read()
        data["token_expires_at"] = "1900000000"
        self._write(data)
        self.assertEqual(load_config(self.path).token_expires_at, 0)

    @unittest.skipIf(sys.platform == "win32", "DPAPI is available on Windows")
    def test_token_from_another_machine_is_dropped_and_flagged(self):
        self._write({"uid": "1001", "login": "ivan", "token": "AAAA",
                     "refresh_token": "BBBB", "protection": "dpapi"})
        config = load_config(self.path)
        self.assertEqual((config.token, config.refresh_token), ("", ""))
        self.assertFalse(config.is_configured)
        # Пользователю надо сказать, что синхронизация выключена не по его воле.
        self.assertTrue(config.token_unreadable)

    def test_readable_or_absent_token_is_not_flagged(self):
        save_config(_config(), self.path)
        self.assertFalse(load_config(self.path).token_unreadable)
        save_config(SyncConfig(), self.path)
        self.assertFalse(load_config(self.path).token_unreadable)

    def test_empty_token_is_stored_as_empty_not_encrypted(self):
        # На Windows зашифрованная пустая строка непуста, и при чтении токен
        # выглядел бы «сохранённым, но нечитаемым» — предупреждение при каждом запуске.
        save_config(SyncConfig(device_name="Касса"), self.path)
        data = self._read()
        self.assertEqual((data["token"], data["refresh_token"], data["protection"]),
                         ("", "", "none"))

    def test_refresh_token_without_access_token_is_not_kept(self):
        save_config(_config(token=""), self.path)
        self.assertEqual(self._read()["refresh_token"], "")

    def test_token_without_uid_is_not_configured(self):
        self.assertFalse(_config(uid="").is_configured)
        self.assertFalse(_config(token="").is_configured)

    def test_target_id_depends_on_api_account_and_folder(self):
        base = _config(remote_dir="Склад")
        self.assertEqual(base.target_id, _config(remote_dir="/Склад/").target_id)
        for other in (_config(remote_dir="Склад", api_url="https://other.example/v1/disk"),
                      _config(remote_dir="Склад", uid="2002"),
                      _config(remote_dir="Другая")):
            self.assertNotEqual(base.target_id, other.target_id)

    def test_target_id_does_not_depend_on_token_or_login(self):
        # Токен обновляется раз в год, логин можно сменить — место то же.
        self.assertEqual(_config().target_id,
                         _config(token="other", refresh_token="x", login="petr").target_id)
        self.assertNotIn("tok", _config().target_id)


class LegacyMigrationTest(unittest.TestCase):
    """Переход с WebDAV-версии (до 1.9.0): login + пароль приложения."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ozon-cfg-")
        self.path = os.path.join(self.dir, "sync.json")
        self.state_path = os.path.join(self.dir, "sync_state.json")
        self.legacy_target = _legacy_target_id("https://webdav.yandex.ru", "Ivan.Petrov",
                                               "OzonSorter")

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def _write_legacy(self, **extra):
        data = {"url": "https://webdav.yandex.ru", "login": "Ivan.Petrov",
                "password": "AAAA", "password_protection": "none",
                "remote_dir": "OzonSorter", "device_name": "Касса"}
        data.update(extra)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _write_state(self, target):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump({"target": target, "base_rev": 7, "base_sha": "abc"}, f)

    def _state(self):
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _logged_in(self, login="ivan-petrov"):
        config = load_config(self.path)
        config.token, config.uid, config.login = "tok", "1001", login
        return config

    def test_legacy_file_is_not_configured_but_remembers_old_target(self):
        self._write_legacy()
        config = load_config(self.path)
        self.assertFalse(config.is_configured)
        self.assertFalse(config.token_unreadable)
        self.assertEqual(config.login, "")
        self.assertEqual(config.device_name, "Касса")
        self.assertEqual(config.legacy_login, "Ivan.Petrov")
        self.assertEqual(config.legacy_target_id, self.legacy_target)

    def test_legacy_target_matches_what_webdav_version_computed(self):
        # Формула WebDAV-версии: sha256("url|login|папка")[:16].
        import hashlib
        expected = hashlib.sha256(
            "https://webdav.yandex.ru|Ivan.Petrov|OzonSorter".encode()).hexdigest()[:16]
        self.assertEqual(self.legacy_target, expected)

    def test_legacy_fields_survive_save_until_login(self):
        self._write_legacy()
        save_config(load_config(self.path), self.path)
        config = load_config(self.path)
        self.assertEqual((config.legacy_login, config.legacy_target_id),
                         ("Ivan.Petrov", self.legacy_target))

    def test_same_account_adopts_sync_state(self):
        self._write_legacy()
        self._write_state(self.legacy_target)
        config = self._logged_in("ivan-petrov")     # Яндекс вернул логин в своём виде
        self.assertTrue(adopt_legacy_state(config, self.state_path))
        state = self._state()
        self.assertEqual(state["target"], config.target_id)
        self.assertEqual(state["base_rev"], 7)

    def test_login_with_domain_and_case_matches(self):
        self._write_legacy(login="ivan.petrov@yandex.ru")
        legacy = _legacy_target_id("https://webdav.yandex.ru", "ivan.petrov@yandex.ru",
                                   "OzonSorter")
        self._write_state(legacy)
        self.assertTrue(adopt_legacy_state(self._logged_in("Ivan-Petrov"), self.state_path))

    def test_other_account_does_not_adopt_sync_state(self):
        # Чужая база не должна выглядеть продолжением этой.
        self._write_legacy()
        self._write_state(self.legacy_target)
        self.assertFalse(adopt_legacy_state(self._logged_in("someone"), self.state_path))
        self.assertEqual(self._state()["target"], self.legacy_target)

    def test_state_of_other_target_is_not_touched(self):
        self._write_legacy()
        self._write_state("ffffffffffffffff")
        self.assertFalse(adopt_legacy_state(self._logged_in(), self.state_path))
        self.assertEqual(self._state()["target"], "ffffffffffffffff")

    def test_missing_or_broken_state_is_fine(self):
        self._write_legacy()
        self.assertFalse(adopt_legacy_state(self._logged_in(), self.state_path))
        with open(self.state_path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertFalse(adopt_legacy_state(self._logged_in(), self.state_path))

    def test_custom_legacy_server_and_folder_are_respected(self):
        self._write_legacy(url="https://dav.example.ru", remote_dir="Склад")
        self.assertEqual(load_config(self.path).legacy_target_id,
                         _legacy_target_id("https://dav.example.ru", "Ivan.Petrov", "Склад"))

    def test_forget_legacy_removes_fields_from_file(self):
        self._write_legacy()
        config = self._logged_in()
        forget_legacy(config)
        save_config(config, self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("legacy_login", data)
        self.assertNotIn("password", data)
        self.assertEqual(load_config(self.path).login, "ivan-petrov")


if __name__ == "__main__":
    unittest.main()
