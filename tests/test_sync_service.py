"""Синхронизация базы через WebDAV: два «устройства» с отдельными базами и общим
локальным WebDAV-сервером (tests/webdav_fake.py). Базы — настоящие SQLite,
созданные DatabaseManager, как в приложении.

Отдельные группы тестов — отказы и злоумышленные данные на Диске: к папке имеет
доступ любой, у кого есть пароль приложения, поэтому meta.json и скачанная база
считаются недоверенными.
"""
import gzip
import hashlib
import glob
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from sqlalchemy import select

from src import sync_service
from src.database import DatabaseManager, SCHEMA_VERSION
from src.models import Client, DeliveryPoint
from src.sync_service import (
    BACKUP_DIR, CancelToken, KEEP_DAILY_BACKUPS, KEEP_REMOTE_REVISIONS, META_NAME,
    SyncCancelled, SyncError, SyncService, SyncStatus,
)
from src.webdav import WebDavClient, WebDavError
from webdav_fake import FakeWebDavServer

REMOTE_DIR = "OzonSorter"
META_PATH = f"/{REMOTE_DIR}/{META_NAME}"


class Device:
    """Отдельная папка данных: своя база и своё состояние синхронизации."""

    def __init__(self, server, name, target_id="t1"):
        self.dir = tempfile.mkdtemp(prefix=f"ozon-sync-{name}-")
        self.db_path = os.path.join(self.dir, "ozon_sorter.db")
        self.sync = SyncService(
            self.db_path, WebDavClient(server.url, "user", "secret", timeout=5),
            REMOTE_DIR, os.path.join(self.dir, "sync_state.json"), name, target_id=target_id,
        )
        self.open_and_close()  # создать схему, как при первом запуске программы

    def open_and_close(self):
        db = DatabaseManager(db_path=self.db_path)
        db.create_tables()
        db.engine.dispose()

    def add_client(self, ozon_id, name="Тест"):
        db = DatabaseManager(db_path=self.db_path)
        session = db.get_session()
        session.add(Client(ozon_client_id=ozon_id, full_name=name,
                           fixed_delivery_point=DeliveryPoint.KOMSOMOLSKAYA_4))
        session.commit()
        session.close()
        db.engine.dispose()

    def client_ids(self):
        db = DatabaseManager(db_path=self.db_path)
        session = db.get_session()
        try:
            return sorted(session.execute(select(Client.ozon_client_id)).scalars().all())
        finally:
            session.close()
            db.engine.dispose()

    def push(self):
        return self.sync.push(self.sync.check().meta)

    def pull(self):
        return self.sync.pull(self.sync.check().meta)

    def status(self):
        return self.sync.check().status

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def db_bytes(mutate=None):
    """Байты настоящей базы программы; mutate(conn) может её испортить."""
    directory = tempfile.mkdtemp(prefix="ozon-dbbytes-")
    path = os.path.join(directory, "x.db")
    try:
        db = DatabaseManager(db_path=path)
        db.create_tables()
        db.engine.dispose()
        if mutate:
            conn = sqlite3.connect(path)
            mutate(conn)
            conn.commit()
            conn.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def publish(server, raw, rev=1, schema_version=SCHEMA_VERSION, packed=None, **meta_overrides):
    """Выложить на «Диск» произвольное содержимое как ревизию, минуя SyncService,
    — так это мог бы сделать тот, у кого есть доступ к папке."""
    sha = hashlib.sha256(raw).hexdigest()
    name = f"ozon_sorter_rev{rev:06d}_{sha[:8]}.db.gz"
    server.dirs.add(f"/{REMOTE_DIR}")
    server.files[f"/{REMOTE_DIR}/{name}"] = packed if packed is not None else gzip.compress(raw)
    meta = {"rev": rev, "file": name, "sha256": sha, "schema_version": schema_version,
            "device": "чужой", "saved_at": "2026-09-30 12:00:00"}
    meta.update(meta_overrides)
    server.files[META_PATH] = json.dumps(meta).encode("utf-8")
    return meta


class SyncServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.server = FakeWebDavServer()
        self.a = Device(self.server, "pc")
        self.b = Device(self.server, "phone")

    def tearDown(self):
        self.server.stop()
        self.a.cleanup()
        self.b.cleanup()

    def _remote_meta(self):
        return json.loads(self.server.files[META_PATH].decode("utf-8"))

    def _revision_files(self):
        return sorted(p for p in self.server.files if p.startswith(f"/{REMOTE_DIR}/ozon_sorter_rev"))

    def _seed(self):
        """A выложил базу с клиентом 111, B её скачал: обе стороны на одной ревизии."""
        self.a.add_client("111")
        self.a.push()
        self.b.pull()


class SyncServiceTest(SyncServiceTestBase):
    # --- исходные состояния ---

    def test_empty_local_and_empty_remote_is_up_to_date(self):
        self.assertEqual(self.a.status(), SyncStatus.UP_TO_DATE)

    def test_local_data_and_empty_remote_is_local_ahead(self):
        self.a.add_client("111")
        self.assertEqual(self.a.status(), SyncStatus.LOCAL_AHEAD)

    # --- выгрузка и скачивание ---

    def test_first_push_writes_revision_and_meta(self):
        self.a.add_client("111")
        self.assertEqual(self.a.push(), 1)
        meta = self._remote_meta()
        self.assertEqual(meta["rev"], 1)
        self.assertRegex(meta["file"], r"^ozon_sorter_rev000001_[0-9a-f]{8}\.db\.gz$")
        self.assertEqual(meta["device"], "pc")
        self.assertEqual(meta["schema_version"], SCHEMA_VERSION)
        self.assertTrue(meta["file"].endswith(meta["sha256"][:8] + ".db.gz"))
        raw = gzip.decompress(self.server.files[f"/{REMOTE_DIR}/{meta['file']}"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), meta["sha256"])
        self.assertEqual(self.a.status(), SyncStatus.UP_TO_DATE)

    def test_new_device_with_empty_base_pulls_instead_of_conflict(self):
        self.a.add_client("111")
        self.a.push()
        self.assertEqual(self.b.status(), SyncStatus.REMOTE_AHEAD)
        self.assertEqual(self.b.pull(), 1)
        self.assertEqual(self.b.client_ids(), ["111"])
        self.assertEqual(self.b.status(), SyncStatus.UP_TO_DATE)

    def test_changes_travel_both_ways_in_turn(self):
        self._seed()
        self.b.add_client("222")
        self.assertEqual(self.b.status(), SyncStatus.LOCAL_AHEAD)
        self.assertEqual(self.b.push(), 2)
        self.assertEqual(self.a.status(), SyncStatus.REMOTE_AHEAD)
        self.a.pull()
        self.assertEqual(self.a.client_ids(), ["111", "222"])
        self.assertEqual(self.a.status(), SyncStatus.UP_TO_DATE)

    def test_pull_keeps_backup_of_replaced_base_in_presync_folder(self):
        self._seed()
        self.b.add_client("222")
        self.b.push()
        self.a.pull()
        backups = os.listdir(os.path.join(self.a.dir, "backups", "presync"))
        self.assertEqual(len(backups), 1)
        self.assertTrue(backups[0].endswith(".db.bak"))

    def test_presync_backups_are_limited(self):
        self._seed()
        for i in range(sync_service.KEEP_PRESYNC_BACKUPS + 3):
            self.b.add_client(str(500 + i))
            self.b.push()
            self.a.pull()
        backups = os.listdir(os.path.join(self.a.dir, "backups", "presync"))
        self.assertEqual(len(backups), sync_service.KEEP_PRESYNC_BACKUPS)

    def test_reopening_base_does_not_look_like_a_local_change(self):
        # Запуск программы (create_tables + миграции) не должен менять файл базы,
        # иначе каждое открытие выглядело бы правкой и вело к ложным конфликтам.
        self.a.add_client("111")
        self.a.push()
        self.a.open_and_close()
        self.a.open_and_close()
        self.assertEqual(self.a.status(), SyncStatus.UP_TO_DATE)
        self.assertFalse(self.a.sync.local_changed())

    def test_local_changed_needs_no_network(self):
        self.a.add_client("111")
        self.a.push()
        self.a.add_client("222")
        self.server.offline = True
        self.assertTrue(self.a.sync.local_changed())

    def test_lost_state_with_identical_content_is_up_to_date(self):
        self.a.add_client("111")
        self.a.push()
        os.remove(self.a.sync.state_path)
        self.assertEqual(self.a.status(), SyncStatus.UP_TO_DATE)

    def test_state_of_another_target_is_ignored(self):
        self.a.add_client("111")
        self.a.push()
        other = SyncService(self.a.db_path, self.a.sync.client, "Другая", self.a.sync.state_path,
                            "pc", target_id="t2")
        self.assertTrue(other.local_changed())      # для нового места — ещё не синхронизировались
        self.assertFalse(self.a.sync.local_changed())

    # --- конфликт ---

    def _make_conflict(self):
        self._seed()
        self.a.add_client("222")
        self.b.add_client("333")
        self.a.push()

    def test_both_sides_changed_is_conflict(self):
        self._make_conflict()
        self.assertEqual(self.b.status(), SyncStatus.CONFLICT)

    def test_conflict_resolved_by_keeping_local(self):
        self._make_conflict()
        self.assertEqual(self.b.push(), 3)
        self.assertEqual(self.b.status(), SyncStatus.UP_TO_DATE)
        self.a.pull()
        self.assertEqual(self.a.client_ids(), ["111", "333"])
        # Вытесненная версия осталась на Диске отдельной ревизией.
        self.assertTrue(any("rev000002_" in p for p in self.server.files))

    def test_conflict_resolved_by_keeping_remote(self):
        self._make_conflict()
        self.b.pull()
        self.assertEqual(self.b.client_ids(), ["111", "222"])
        self.assertEqual(self.b.status(), SyncStatus.UP_TO_DATE)

    # --- потеря данных: решение принимается по содержимому, а не по номеру ---

    def test_recreated_remote_with_reused_revision_number_is_not_up_to_date(self):
        self._seed()                                  # обе стороны на rev1
        self.a.add_client("222")
        self.a.push()
        self.a.add_client("333")
        self.a.push()                                 # rev3
        self.server.files.clear()                     # папку на Диске очистили
        self.server.dirs = {"/"}
        self.assertEqual(self.a.status(), SyncStatus.REMOTE_MISSING)
        self.a.sync.push(None)                        # пользователь согласился выложить заново: rev1
        # У B тот же номер ревизии, но другое содержимое: это не «совпадает с Диском».
        self.assertEqual(self.b.status(), SyncStatus.REMOTE_AHEAD)

    def test_recreated_remote_with_reused_revision_number_and_local_changes_is_conflict(self):
        # Тот же номер ревизии, другое содержимое, а у B есть свои правки: молча
        # выложить их поверх чужой базы нельзя — это конфликт.
        self._seed()
        self.a.add_client("222")
        self.a.push()
        self.server.files.clear()
        self.server.dirs = {"/"}
        self.a.sync.push(None)                        # на Диске снова rev1, но другая база
        self.b.add_client("444")
        self.assertEqual(self.b.status(), SyncStatus.CONFLICT)

    def test_remote_missing_only_for_device_with_data_that_synced_before(self):
        self.a.add_client("111")
        self.a.push()
        self.server.files.clear()
        self.server.dirs = {"/"}
        self.assertEqual(self.a.status(), SyncStatus.REMOTE_MISSING)
        # Другое устройство, у которого ещё не было синхронизации: обычная первая выгрузка.
        self.b.add_client("222")
        self.assertEqual(self.b.status(), SyncStatus.LOCAL_AHEAD)
        # И пустая база не поднимает тревогу.
        empty = Device(self.server, "empty")
        try:
            self.assertEqual(empty.status(), SyncStatus.UP_TO_DATE)
        finally:
            empty.cleanup()

    def test_push_refuses_when_remote_changed_since_check(self):
        self._seed()
        self.a.add_client("222")
        stale = self.a.sync.check()                   # A решил выложить, глядя на rev1
        self.b.add_client("333")
        self.b.push()                                 # пока A думал, B выложил rev2
        with self.assertRaises(SyncError):
            self.a.sync.push(stale.meta)
        self.assertEqual(self._remote_meta()["rev"], 2)
        self.assertEqual(self._remote_meta()["device"], "phone")

    def test_push_detects_change_that_happens_during_upload(self):
        self._seed()
        self.a.add_client("222")
        check = self.a.sync.check()
        self.b.add_client("333")
        original_put = self.a.sync.client.put
        fired = []

        def put_then_other_device_pushes(path, data, timeout=None):
            original_put(path, data, timeout=timeout)
            if path.endswith(".db.gz") and not fired:
                fired.append(True)
                self.b.push()                         # B успевает раньше A

        self.a.sync.client.put = put_then_other_device_pushes
        with self.assertRaises(SyncError):
            self.a.sync.push(check.meta)
        self.assertEqual(self._remote_meta()["device"], "phone")

    def test_concurrent_writers_do_not_overwrite_each_others_revision_files(self):
        self.a.add_client("111")
        self.b.add_client("222")
        check_a, check_b = self.a.sync.check(), self.b.sync.check()   # оба видят пустой Диск
        self.a.sync.push(check_a.meta)
        with self.assertRaises(SyncError):
            self.b.sync.push(check_b.meta)
        # Оба файла ревизии 1 лежат рядом, под разными именами.
        self.assertEqual(len([p for p in self._revision_files() if "rev000001_" in p]), 2)
        # И meta.json указывает на целый файл A.
        third = Device(self.server, "third")
        try:
            third.pull()
            self.assertEqual(third.client_ids(), ["111"])
        finally:
            third.cleanup()

    def test_pull_refuses_when_remote_changed_after_user_confirmed(self):
        self.a.add_client("111")
        self.a.push()
        shown = self.b.sync.check()                   # пользователю показали rev1
        self.a.add_client("222")
        self.a.push()                                 # а на Диске уже rev2
        self.b.add_client("999")
        with self.assertRaises(SyncError):
            self.b.sync.pull(shown.meta)
        self.assertEqual(self.b.client_ids(), ["999"])

    def test_deleted_local_file_is_replaced_from_disk_not_pushed(self):
        self.a.add_client("111")
        self.a.push()
        os.remove(self.a.db_path)
        check = self.a.sync.check()
        self.assertEqual(check.status, SyncStatus.REMOTE_AHEAD)
        self.a.sync.pull(check.meta)
        self.assertEqual(self.a.client_ids(), ["111"])

    # --- откат версии на Диске ---

    def test_older_revision_on_disk_is_flagged(self):
        self._seed()                                  # обе на rev1
        meta_rev1 = self.server.files[META_PATH]
        self.a.add_client("222")
        self.a.push()
        self.b.pull()                                 # B на rev2
        self.server.files[META_PATH] = meta_rev1      # папку «восстановили из старой копии»
        check = self.b.sync.check()
        self.assertEqual(check.status, SyncStatus.REMOTE_AHEAD)
        self.assertTrue(check.remote_is_older)

    def test_older_revision_with_local_changes_is_conflict_and_flagged(self):
        self._seed()
        meta_rev1 = self.server.files[META_PATH]
        self.a.add_client("222")
        self.a.push()
        self.b.pull()
        self.b.add_client("333")
        self.server.files[META_PATH] = meta_rev1
        check = self.b.sync.check()
        self.assertEqual(check.status, SyncStatus.CONFLICT)
        self.assertTrue(check.remote_is_older)

    def test_normal_newer_revision_is_not_flagged_as_older(self):
        self._seed()
        self.a.add_client("222")
        self.a.push()
        self.assertFalse(self.b.sync.check().remote_is_older)

    # --- отказы при передаче ---

    def test_interrupted_push_leaves_previous_revision_current(self):
        self.a.add_client("111")
        self.a.push()
        self.a.add_client("222")
        original_put = self.a.sync.client.put

        def put_failing_on_meta(path, data, timeout=None):
            if path.endswith(META_NAME):
                raise WebDavError("PUT failed", 507)
            return original_put(path, data, timeout=timeout)

        self.a.sync.client.put = put_failing_on_meta
        with self.assertRaises(WebDavError):
            self.a.push()
        self.a.sync.client.put = original_put

        self.assertEqual(self._remote_meta()["rev"], 1)
        self.assertEqual(self.a.status(), SyncStatus.LOCAL_AHEAD)
        self.b.pull()
        self.assertEqual(self.b.client_ids(), ["111"])
        # Повторная выгрузка проходит и занимает ту же ревизию 2.
        self.assertEqual(self.a.push(), 2)

    def test_unreachable_server_raises_network_error(self):
        self.a.add_client("111")
        self.server.offline = True
        with self.assertRaises(WebDavError) as ctx:
            self.a.sync.check()
        self.assertTrue(ctx.exception.is_network_error)

    def test_pull_with_empty_remote_raises(self):
        with self.assertRaises(SyncError):
            self.a.sync.pull()

    def test_leftover_journal_blocks_sync(self):
        self.a.add_client("111")
        with open(self.a.db_path + "-journal", "wb") as f:
            f.write(b"x")
        with self.assertRaises(SyncError):
            self.a.sync.push(None)

    def test_replace_retries_when_file_is_briefly_locked(self):
        self._seed()
        self.a.add_client("222")
        self.a.push()
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            if dst == self.b.db_path and calls["n"] < 2:
                calls["n"] += 1
                raise PermissionError(13, "locked")
            return real_replace(src, dst)

        with mock.patch("src.sync_service.os.replace", side_effect=flaky_replace), \
                mock.patch("src.sync_service.time.sleep"):
            self.b.pull()
        self.assertEqual(self.b.client_ids(), ["111", "222"])

    def test_replace_gives_readable_error_when_file_stays_locked(self):
        self._seed()
        self.a.add_client("222")
        self.a.push()
        real_replace = os.replace

        def always_locked(src, dst):
            if dst == self.b.db_path:
                raise PermissionError(13, "locked")
            return real_replace(src, dst)

        with mock.patch("src.sync_service.os.replace", side_effect=always_locked), \
                mock.patch("src.sync_service.time.sleep"):
            with self.assertRaises(SyncError):
                self.b.pull()
        self.assertEqual(self.b.client_ids(), ["111"])
        self.assertFalse(os.path.exists(self.b.db_path + ".sync-tmp"))

    # --- обслуживание ---

    def test_old_revisions_pruned(self):
        total = KEEP_REMOTE_REVISIONS + 3
        for i in range(total):
            self.a.add_client(str(1000 + i))
            self.a.push()
        revisions = self._revision_files()
        self.assertEqual(len(revisions), KEEP_REMOTE_REVISIONS)
        self.assertRegex(revisions[-1], rf"rev{total:06d}_")

    def test_pulled_base_keeps_schema_version(self):
        self.a.add_client("111")
        self.a.push()
        self.b.pull()
        conn = sqlite3.connect(self.b.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        finally:
            conn.close()


class StateAndRevisionTest(SyncServiceTestBase):
    def test_same_revision_replaced_by_another_base_is_flagged(self):
        self._seed()
        self.a.add_client("222")
        self.a.push()                                  # rev2
        other = db_bytes(lambda c: c.execute(
            "INSERT INTO clients (ozon_client_id, is_active, created_at, updated_at) "
            "VALUES ('777', 1, '2026-01-01', '2026-01-01')"))
        publish(self.server, other, rev=2)             # та же ревизия 2, но другая база
        check = self.a.sync.check()
        self.assertEqual(check.status, SyncStatus.REMOTE_AHEAD)
        self.assertTrue(check.remote_is_older)

    def test_revision_number_continues_from_highest_seen_after_disk_rewind(self):
        self._seed()
        meta_rev1 = self.server.files[META_PATH]
        self.a.add_client("222")
        self.a.push()                                  # rev2
        self.b.pull()                                  # B видел rev2
        self.server.files[META_PATH] = meta_rev1       # Диск откатили на rev1
        self.b.add_client("333")
        self.assertEqual(self.b.push(), 3)             # не 2: иначе у других «версия старше»
        self.assertFalse(self.a.sync.check().remote_is_older)

    def test_up_to_date_check_does_not_rewrite_state_file(self):
        self.a.add_client("111")
        self.a.push()
        with mock.patch.object(self.a.sync, "_save_state") as save:
            self.assertEqual(self.a.status(), SyncStatus.UP_TO_DATE)
        save.assert_not_called()

    def test_state_write_failure_after_push_does_not_fail_the_exchange(self):
        self.a.add_client("111")
        with mock.patch.object(self.a.sync, "_save_state", side_effect=PermissionError(13, "locked")):
            self.assertEqual(self.a.push(), 1)
        self.assertIn(META_PATH, self.server.files)
        self.assertEqual(self.a.status(), SyncStatus.UP_TO_DATE)   # состояние восстановилось по sha

    def test_state_write_failure_after_pull_keeps_pulled_base(self):
        self.a.add_client("111")
        self.a.push()
        check = self.b.sync.check()
        with mock.patch.object(self.b.sync, "_save_state", side_effect=PermissionError(13, "locked")):
            self.assertEqual(self.b.sync.pull(check.meta), 1)
        self.assertEqual(self.b.client_ids(), ["111"])
        self.assertEqual(self.b.status(), SyncStatus.UP_TO_DATE)

    def test_no_temporary_state_files_left(self):
        self.a.add_client("111")
        self.a.push()
        self.a.status()
        self.assertEqual(glob.glob(self.a.sync.state_path + "*.tmp"), [])

    def test_state_is_saved_even_if_replace_needs_retries(self):
        self.a.add_client("111")
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            if dst == self.a.sync.state_path and calls["n"] < 2:
                calls["n"] += 1
                raise PermissionError(13, "locked")
            return real_replace(src, dst)

        with mock.patch("src.sync_service.os.replace", side_effect=flaky), \
                mock.patch("src.sync_service.time.sleep"):
            self.a.push()
        self.assertEqual(calls["n"], 2)
        self.assertFalse(self.a.sync.local_changed())

    # --- время последней синхронизации ---

    def _state(self, device):
        with open(device.sync.state_path, encoding="utf-8") as f:
            return json.load(f)

    def _set_synced_at(self, device, value):
        state = self._state(device)
        if value is None:
            state.pop("synced_at", None)
        else:
            state["synced_at"] = value
        with open(device.sync.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def _assert_recent(self, value):
        moment = datetime.strptime(value, sync_service.SAVED_AT_FORMAT)
        self.assertLess(abs((datetime.now() - moment).total_seconds()), 60)

    def test_push_and_pull_record_sync_time(self):
        self._seed()
        self._assert_recent(self._state(self.a)["synced_at"])
        self._assert_recent(self._state(self.b)["synced_at"])

    def test_conflict_reports_time_of_last_local_sync(self):
        self._seed()
        self._set_synced_at(self.b, "2026-09-29 08:15:00")
        self.a.add_client("222")
        self.a.push()
        self.b.add_client("333")
        check = self.b.sync.check()
        self.assertEqual(check.status, SyncStatus.CONFLICT)
        self.assertEqual(check.local_synced_at, "2026-09-29 08:15:00")

    def test_missing_or_malformed_sync_time_is_unknown(self):
        self._seed()
        self.a.add_client("222")
        self.a.push()
        self.b.add_client("333")
        for value in (None, "<b>вчера</b>", 12345):
            self._set_synced_at(self.b, value)
            check = self.b.sync.check()
            self.assertEqual(check.status, SyncStatus.CONFLICT)   # состояние читается
            self.assertEqual(check.local_synced_at, "")

    def test_rollback_restores_previous_sync_time(self):
        self._seed()
        self._set_synced_at(self.b, "2026-09-29 08:15:00")
        self.a.add_client("222")
        self.a.push()
        self.b.pull()
        self.b.sync.rollback_pull()
        self.assertEqual(self._state(self.b)["synced_at"], "2026-09-29 08:15:00")


class DataLossProtectionTest(SyncServiceTestBase):
    """База на Диске не должна стереться по ошибке: пустой базой, базой
    устройства, ни разу не загружавшего Диск, или цепочкой выгрузок."""

    BACKUP_PREFIX = f"/{REMOTE_DIR}/{BACKUP_DIR}/"

    def _today_backup(self):
        return f"{self.BACKUP_PREFIX}ozon_sorter_{datetime.now().strftime('%Y-%m-%d')}.db.gz"

    def _backups(self):
        return sorted(p for p in self.server.files if p.startswith(self.BACKUP_PREFIX))

    def test_empty_base_is_never_pushed_over_disk(self):
        self.a.add_client("111")
        self.a.push()
        meta_before = self.server.files[META_PATH]
        for prepare in (lambda: None, lambda: os.remove(self.b.db_path)):
            prepare()
            if not os.path.exists(self.b.db_path):
                self.b.open_and_close()
            with self.assertRaises(SyncError) as ctx:
                self.b.sync.push(self.b.sync.check().meta)
            self.assertIn("пустая база", str(ctx.exception))
        self.assertEqual(self.server.files[META_PATH], meta_before)

    def test_never_synced_device_cannot_replace_disk_base(self):
        self.a.add_client("111")
        self.a.push()
        self.b.add_client("999")                       # работал с нуля, Диск не загружал
        check = self.b.sync.check()
        self.assertEqual(check.status, SyncStatus.CONFLICT)
        self.assertTrue(check.never_synced)
        meta_before = self.server.files[META_PATH]
        with self.assertRaises(SyncError) as ctx:
            self.b.sync.push(check.meta)
        self.assertIn("ни разу не загружало", str(ctx.exception))
        self.assertEqual(self.server.files[META_PATH], meta_before)
        self.b.pull()                                  # «Взять с Диска» по-прежнему можно
        self.assertEqual(self.b.client_ids(), ["111"])

    def test_synced_device_keeps_conflict_choice(self):
        self._seed()
        self.a.add_client("222")
        self.a.push()
        self.b.add_client("333")
        check = self.b.sync.check()
        self.assertFalse(check.never_synced)
        self.assertEqual(self.b.sync.push(check.meta), 3)

    def test_first_push_to_empty_disk_is_allowed(self):
        self.a.add_client("111")
        self.assertEqual(self.a.push(), 1)

    def test_daily_backup_made_once_per_day(self):
        self.a.add_client("111")
        self.a.push()
        first = self.server.files[self._today_backup()]
        with open(self.a.db_path, "rb") as f:
            self.assertEqual(gzip.decompress(first), f.read())
        self.a.add_client("222")
        self.a.push()
        self.assertEqual(self.server.files[self._today_backup()], first)  # не перезаписана
        self.assertEqual(self._backups(), [self._today_backup()])

    def test_revision_pruning_keeps_daily_backups(self):
        old = f"{self.BACKUP_PREFIX}ozon_sorter_2026-01-01.db.gz"
        self.server.dirs.update({f"/{REMOTE_DIR}", f"/{REMOTE_DIR}/{BACKUP_DIR}"})
        self.server.files[old] = b"old"
        for i in range(KEEP_REMOTE_REVISIONS + 3):
            self.a.add_client(str(1000 + i))
            self.a.push()
        self.assertIn(old, self.server.files)
        self.assertIn(self._today_backup(), self.server.files)

    def test_daily_backups_limited_and_foreign_files_kept(self):
        self.server.dirs.update({f"/{REMOTE_DIR}", f"/{REMOTE_DIR}/{BACKUP_DIR}"})
        # 35 старых копий: 31 за январь и 4 за февраль.
        for month, days in ((1, 31), (2, 4)):
            for day in range(1, days + 1):
                self.server.files[f"{self.BACKUP_PREFIX}ozon_sorter_2026-{month:02d}-{day:02d}.db.gz"] = b"x"
        foreign = f"{self.BACKUP_PREFIX}заметки.txt"
        self.server.files[foreign] = b"keep"
        self.a.add_client("111")
        self.a.push()
        dated = [p for p in self._backups() if p.endswith(".db.gz")]
        self.assertEqual(len(dated), KEEP_DAILY_BACKUPS)
        self.assertIn(self._today_backup(), dated)
        self.assertNotIn(f"{self.BACKUP_PREFIX}ozon_sorter_2026-01-01.db.gz", dated)
        self.assertIn(foreign, self.server.files)

    def test_backup_failure_does_not_fail_push(self):
        self.a.add_client("111")
        real = self.a.sync.client.list_dir

        def broken(path):
            if BACKUP_DIR in path:
                raise WebDavError("boom")
            return real(path)

        with mock.patch.object(self.a.sync.client, "list_dir", side_effect=broken):
            self.assertEqual(self.a.push(), 1)
        self.assertFalse(self.a.sync.local_changed())


class HostileRemoteTest(SyncServiceTestBase):
    """Недоверенное содержимое Диска: ни при каких условиях не должно заменить
    рабочую базу или сломать запуск программы."""

    def setUp(self):
        super().setUp()
        self.b.add_client("999")     # у B есть что терять

    def _assert_b_untouched(self):
        self.assertEqual(self.b.client_ids(), ["999"])
        self.assertFalse(os.path.exists(self.b.db_path + ".sync-tmp"))
        self.assertFalse(os.path.exists(os.path.join(self.b.dir, "backups", "presync")))
        self.b.open_and_close()      # и программа по-прежнему открывает базу

    # --- содержимое базы ---

    def test_non_sqlite_content_with_valid_checksum_rejected(self):
        publish(self.server, b"this is not sqlite at all " * 40)
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_random_bytes_after_sqlite_header_rejected(self):
        publish(self.server, b"SQLite format 3\x00" + os.urandom(8192))
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_base_with_trigger_rejected(self):
        raw = db_bytes(lambda c: c.execute(
            "CREATE TRIGGER pwn AFTER INSERT ON clients "
            "BEGIN UPDATE clients SET full_name = 'pwned'; END"))
        publish(self.server, raw)
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_base_with_view_rejected(self):
        publish(self.server, db_bytes(lambda c: c.execute("CREATE VIEW v AS SELECT * FROM clients")))
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_base_with_foreign_table_rejected(self):
        publish(self.server, db_bytes(lambda c: c.execute("CREATE TABLE evil (x)")))
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_schema_version_is_taken_from_the_file_not_only_from_meta(self):
        raw = db_bytes(lambda c: c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 50}"))
        publish(self.server, raw, schema_version=SCHEMA_VERSION)   # meta «честная», файл — нет
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_newer_schema_in_meta_rejected_before_download(self):
        publish(self.server, db_bytes(), schema_version=SCHEMA_VERSION + 1)
        with self.assertRaises(SyncError) as ctx:
            self.b.pull()
        self.assertIn("Обновите программу", str(ctx.exception))
        self._assert_b_untouched()

    def test_checksum_mismatch_rejected(self):
        meta = publish(self.server, db_bytes())
        name = f"/{REMOTE_DIR}/{meta['file']}"
        self.server.files[name] = gzip.compress(db_bytes(lambda c: c.execute("PRAGMA user_version = 1")) + b"x")
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    # --- архив ---

    def test_not_gzip_rejected(self):
        publish(self.server, db_bytes(), packed=b"\x00\x01 not gzip")
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_gzip_with_broken_deflate_stream_rejected_with_readable_error(self):
        broken = b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\xff" * 64
        publish(self.server, db_bytes(), packed=broken)
        with self.assertRaises(SyncError):
            self.b.pull()
        self._assert_b_untouched()

    def test_gzip_bomb_rejected(self):
        raw = b"\x00" * 100_000
        publish(self.server, raw)
        reads = []
        real_read = gzip.GzipFile.read

        def spy(file, size=-1):
            reads.append(size)
            return real_read(file, size)

        with mock.patch.object(sync_service, "MAX_DB_BYTES", 10_000), \
                mock.patch.object(gzip.GzipFile, "read", spy):
            with self.assertRaises(SyncError) as ctx:
                self.b.pull()
        self.assertIn("слишком большой", str(ctx.exception))
        # Распаковка ограничена: в память не читается больше лимита (+1 байт для
        # обнаружения превышения), сколько бы архив ни обещал.
        self.assertEqual(reads, [10_001])
        self._assert_b_untouched()

    def test_oversized_meta_rejected(self):
        publish(self.server, db_bytes())
        self.server.files[META_PATH] = b" " * (70 * 1024)
        with self.assertRaises(WebDavError):
            self.b.sync.remote_meta()

    # --- meta.json ---

    def _meta_with(self, **changes):
        publish(self.server, db_bytes())
        meta = self._remote_meta()
        meta.update(changes)
        self.server.files[META_PATH] = json.dumps(meta).encode("utf-8")

    def test_meta_with_invalid_values_rejected(self):
        good = db_bytes()
        sha = hashlib.sha256(good).hexdigest()
        cases = {
            "rev zero": {"rev": 0},
            "rev negative": {"rev": -5},
            "rev too large": {"rev": 999_999},
            "rev bool": {"rev": True},
            "rev float": {"rev": 1.5},
            "rev string": {"rev": "1"},
            "schema bool": {"schema_version": True},
            "schema negative": {"schema_version": -1},
            "sha upper case": {"sha256": sha.upper()},
            "sha short": {"sha256": sha[:40]},
            "file of another revision": {"file": f"ozon_sorter_rev000007_{sha[:8]}.db.gz"},
            "file of another content": {"file": "ozon_sorter_rev000001_deadbeef.db.gz"},
            "file with path": {"file": f"../ozon_sorter_rev000001_{sha[:8]}.db.gz"},
            "file with trailing newline": {"file": f"ozon_sorter_rev000001_{sha[:8]}.db.gz\n"},
            "file with unicode digits": {"file": f"ozon_sorter_rev００００１_{sha[:8]}.db.gz"},
            "file not a string": {"file": 5},
        }
        for label, changes in cases.items():
            with self.subTest(label):
                publish(self.server, good)
                meta = self._remote_meta()
                meta.update(changes)
                self.server.files[META_PATH] = json.dumps(meta).encode("utf-8")
                with self.assertRaises(SyncError):
                    self.b.sync.remote_meta()

    def test_broken_meta_json_rejected(self):
        publish(self.server, db_bytes())
        for raw in (b"{not json", b"[]", b"null", b"\xff\xfe", b'{"rev": 1}'):
            with self.subTest(raw=raw):
                self.server.files[META_PATH] = raw
                with self.assertRaises(SyncError):
                    self.b.sync.remote_meta()

    def test_meta_text_shown_in_dialogs_is_sanitised(self):
        self._meta_with(device='<img src="file://attacker/x.png"><h1>Пароль?</h1>&\n\x00ПК',
                        saved_at="когда-нибудь")
        meta = self.b.sync.remote_meta()
        for ch in '<>&\n\x00':
            self.assertNotIn(ch, meta.device)
        self.assertIn("ПК", meta.device)
        self.assertEqual(meta.saved_at, "")

    def test_meta_device_name_is_length_limited(self):
        self._meta_with(device="х" * 500)
        self.assertLessEqual(len(self.b.sync.remote_meta().device), 64)

    def test_valid_saved_at_is_kept(self):
        self._meta_with(saved_at="2026-09-30 18:00:00")
        self.assertEqual(self.b.sync.remote_meta().saved_at, "2026-09-30 18:00:00")

    # --- защита от порчи со своей стороны ---

    def test_damaged_local_base_is_not_pushed(self):
        with open(self.b.db_path, "wb") as f:
            f.write(b"garbage " * 500)
        with self.assertRaises(SyncError):
            self.b.sync.push(None)
        self.assertNotIn(META_PATH, self.server.files)

    def test_local_base_with_trigger_is_not_pushed(self):
        conn = sqlite3.connect(self.b.db_path)
        conn.execute("CREATE TRIGGER t AFTER INSERT ON clients BEGIN SELECT 1; END")
        conn.commit()
        conn.close()
        with self.assertRaises(SyncError):
            self.b.sync.push(None)
        self.assertNotIn(META_PATH, self.server.files)

    def test_connections_ignore_untrusted_schema(self):
        db = DatabaseManager(db_path=self.b.db_path)
        try:
            with db.engine.connect() as conn:
                value = conn.exec_driver_sql("PRAGMA trusted_schema").scalar()
            self.assertEqual(value, 0)
        finally:
            db.engine.dispose()

    # --- откат после неудачной загрузки ---

    def test_rollback_restores_previous_base_and_state(self):
        self.a.add_client("111")
        self.a.push()
        check = self.b.sync.check()
        self.b.sync.pull(check.meta)
        self.assertEqual(self.b.client_ids(), ["111"])
        self.b.sync.rollback_pull()
        self.assertEqual(self.b.client_ids(), ["999"])
        self.assertTrue(self.b.sync.local_changed())      # состояние вернулось к прежнему

    def test_rollback_without_previous_file_removes_pulled_base(self):
        self.a.add_client("111")
        self.a.push()
        os.remove(self.b.db_path)
        self.b.pull()
        self.assertTrue(os.path.exists(self.b.db_path))
        self.b.sync.rollback_pull()
        self.assertFalse(os.path.exists(self.b.db_path))

    def test_rollback_can_be_retried_after_failed_replace(self):
        self.a.add_client("111")
        self.a.push()
        self.b.pull()
        with mock.patch("src.sync_service.os.replace", side_effect=PermissionError(13, "busy")), \
                mock.patch("src.sync_service.time.sleep"):
            with self.assertRaises(SyncError):
                self.b.sync.rollback_pull()
        self.assertFalse(os.path.exists(self.b.db_path + ".sync-tmp"))
        self.b.sync.rollback_pull()                    # данные для отката не потеряны
        self.assertEqual(self.b.client_ids(), ["999"])

    def test_rollback_without_pull_does_nothing(self):
        self.b.sync.rollback_pull()
        self.assertEqual(self.b.client_ids(), ["999"])


class CancelTokenTest(SyncServiceTestBase):
    def test_cancel_before_commit_is_accepted_and_stops_commit(self):
        token = CancelToken()
        self.assertTrue(token.cancel())
        with self.assertRaises(SyncCancelled):
            token.commit()

    def test_cancel_after_commit_is_refused(self):
        token = CancelToken()
        token.commit()
        self.assertFalse(token.cancel())

    def test_cancelled_pull_leaves_local_base_untouched(self):
        self.a.add_client("111")
        self.a.push()
        self.b.add_client("999")
        check = self.b.sync.check()
        token = CancelToken()
        token.cancel()
        with self.assertRaises(SyncCancelled):
            self.b.sync.pull(check.meta, token)
        self.assertEqual(self.b.client_ids(), ["999"])
        self.assertFalse(os.path.exists(self.b.db_path + ".sync-tmp"))
        self.assertFalse(os.path.exists(os.path.join(self.b.dir, "backups", "presync")))

    def test_cancelled_push_does_not_switch_current_revision(self):
        self.a.add_client("111")
        self.a.push()
        self.a.add_client("222")
        check = self.a.sync.check()
        token = CancelToken()
        token.cancel()
        with self.assertRaises(SyncCancelled):
            self.a.sync.push(check.meta, token)
        self.assertEqual(self._remote_meta()["rev"], 1)
        self.assertTrue(self.a.sync.local_changed())


if __name__ == "__main__":
    unittest.main()
