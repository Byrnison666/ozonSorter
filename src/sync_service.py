"""Синхронизация базы через WebDAV (Яндекс.Диск): обмен целым файлом SQLite.

Рассчитано на работу по очереди: перед работой скачать, после — выложить.
Слияния нет; если изменены обе стороны, это конфликт, и пользователь выбирает
версию. На сервере каждая выгрузка — отдельный файл ревизии, а meta.json
указывает на текущую: переключение ревизии — одна запись маленького файла.

Всё, что приходит с Диска, считается недоверенным: к папке имеет доступ любой,
у кого есть аккаунт или пароль приложения. Поэтому meta.json разбирается
строго, а скачанная база проверяется до того, как заменит рабочую.
"""
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from .database import SCHEMA_VERSION
from .models import Base
from .webdav import WebDavClient, WebDavError

META_NAME = "meta.json"
KEEP_REMOTE_REVISIONS = 5
KEEP_PRESYNC_BACKUPS = 10
_MAX_META_BYTES = 64 * 1024
# Распакованная база больше этого — не наша: защита от «gzip-бомбы» в файле с Диска.
MAX_DB_BYTES = 512 * 1024 * 1024
_MAX_REV = 999_998
# В имени — хвост sha: два устройства, взявшие один номер ревизии, не затрут
# файлы друг друга, и meta.json всегда указывает на файл со своим содержимым.
_REVISION_RE = re.compile(r"ozon_sorter_rev(\d{6})_([0-9a-f]{8})\.db\.gz", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SAVED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"
# Объекты, которые программа создаёт сама. Всё остальное в базе с Диска —
# постороннее: триггер или представление исполнялись бы внутри программы.
# Таблицы берутся из моделей, чтобы новая таблица не отвергалась при синхронизации.
_ALLOWED_TABLES = frozenset(Base.metadata.tables) | {"sqlite_sequence", "sqlite_stat1"}


class SyncStatus(str, Enum):
    UP_TO_DATE = "UP_TO_DATE"
    LOCAL_AHEAD = "LOCAL_AHEAD"        # изменения только здесь — выложить
    REMOTE_AHEAD = "REMOTE_AHEAD"      # на Диске другая версия, здесь правок нет — скачать
    CONFLICT = "CONFLICT"              # изменены обе стороны
    REMOTE_MISSING = "REMOTE_MISSING"  # с Диска пропала база, с которой уже синхронизировались


class SyncError(Exception):
    """Синхронизацию нельзя выполнить; текст ошибки показывается пользователю."""


class SyncCancelled(Exception):
    """Пользователь отменил операцию до того, как она что-либо изменила."""


class CancelToken:
    """Отмена долгой операции из другого потока.

    Операция вызывает commit() перед необратимым шагом. Отмена, пришедшая
    раньше, прерывает операцию; пришедшая позже — отклоняется, и вызывающий
    дожидается конца. Так «отменённая» загрузка не заменит базу под уже
    работающей программой.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._committed = False

    def cancel(self) -> bool:
        """True — отмена принята; False — необратимый шаг уже начат."""
        with self._lock:
            if self._committed:
                return False
            self._cancelled = True
            return True

    def commit(self) -> None:
        with self._lock:
            if self._cancelled:
                raise SyncCancelled()
            self._committed = True


@dataclass(frozen=True)
class RemoteMeta:
    rev: int
    file: str
    sha256: str
    schema_version: int
    device: str
    saved_at: str


@dataclass(frozen=True)
class SyncCheck:
    status: SyncStatus
    meta: Optional[RemoteMeta]       # текущая ревизия на Диске; None — Диск пуст
    # Диск не продолжает ту версию, с которой работал этот компьютер: номер ревизии
    # меньше уже виденного либо та же ревизия заменена другой базой.
    remote_is_older: bool = False


def _replace_file(src: str, dst: str, attempts: int = 10, delay: float = 0.3) -> None:
    """os.replace с повторами: на Windows файл сразу после закрытия могут держать
    антивирус или индексатор. После последней попытки поднимает PermissionError."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _same_revision(a: Optional[RemoteMeta], b: Optional[RemoteMeta]) -> bool:
    if a is None or b is None:
        return a is b
    return a.rev == b.rev and a.sha256 == b.sha256


def _parse_meta(raw: bytes) -> RemoteMeta:
    data = json.loads(raw.decode("utf-8"))
    rev, file, sha = data["rev"], data["file"], data["sha256"]
    schema_version = data["schema_version"]
    # type(...) is int: bool и float сюда не проходят.
    if type(rev) is not int or not 1 <= rev <= _MAX_REV:
        raise ValueError("bad rev")
    if type(schema_version) is not int or not 0 <= schema_version <= 1_000_000:
        raise ValueError("bad schema_version")
    if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
        raise ValueError("bad sha256")
    # Имя файла приходит с сервера — не даём ему указывать за пределы папки и
    # требуем, чтобы оно соответствовало ревизии и содержимому.
    match = _REVISION_RE.fullmatch(file) if isinstance(file, str) else None
    if not match or int(match.group(1)) != rev or match.group(2) != sha[:8]:
        raise ValueError("bad file name")

    # Эти строки показываются в диалогах — убираем управляющие символы и разметку.
    device = "".join(
        ch for ch in str(data.get("device", "")) if ch.isprintable() and ch not in "<>&"
    )[:64]
    saved_at = str(data.get("saved_at", ""))
    try:
        datetime.strptime(saved_at, _SAVED_AT_FORMAT)
    except ValueError:
        saved_at = ""
    return RemoteMeta(rev, file, sha, schema_version, device, saved_at)


def _validate_database(path: str) -> None:
    """Проверить файл базы до того, как он заменит рабочую (или уйдёт на Диск)."""
    try:
        conn = sqlite3.connect(f"file:{urllib.request.pathname2url(path)}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise SyncError(f"Файл базы не открывается: {e}") from e
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SyncError("Файл базы повреждён.")
        if conn.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
            raise SyncError(
                "База создана более новой версией программы. "
                "Обновите программу на этом устройстве."
            )
        for kind, name in conn.execute("SELECT type, name FROM sqlite_master"):
            if kind in ("trigger", "view") or (kind == "table" and name not in _ALLOWED_TABLES):
                raise SyncError("База содержит посторонние объекты и не может быть использована.")
    except sqlite3.DatabaseError as e:
        raise SyncError(f"Файл не является базой программы: {e}") from e
    finally:
        conn.close()


class SyncService:
    def __init__(self, db_path: str, client: WebDavClient, remote_dir: str,
                 state_path: str, device_name: str,
                 transfer_timeout: Optional[float] = None, target_id: str = ""):
        self.db_path = db_path
        self.client = client
        self.remote_dir = remote_dir.strip("/")
        self.state_path = state_path
        self.device_name = device_name
        # Передача файла базы идёт дольше служебных запросов.
        self.transfer_timeout = transfer_timeout
        # К чему относится сохранённое состояние (сервер, аккаунт, папка). После
        # смены настроек старое состояние к новому месту неприменимо.
        self.target_id = target_id
        self._rollback = None

    # --- состояние ---

    def _load_state(self) -> dict:
        empty = {"base_rev": 0, "base_sha256": ""}
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("target", "") != self.target_id:
                return empty
            return {"base_rev": int(state["base_rev"]), "base_sha256": str(state["base_sha256"])}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Файла нет или он повреждён — считаем, что ещё не синхронизировались.
            return empty

    def _save_state(self, rev: int, sha256: str) -> None:
        # Имя временного файла уникально: проверка при запуске и ручная
        # синхронизация могут работать одновременно.
        tmp = f"{self.state_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"base_rev": rev, "base_sha256": sha256, "target": self.target_id}, f)
                f.flush()
                os.fsync(f.fileno())
            _replace_file(tmp, self.state_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _remember(self, rev: int, sha256: str) -> None:
        """Запомнить ревизию после состоявшегося обмена. Сбой записи — не сбой
        обмена: check() восстановит состояние по совпадению содержимого."""
        try:
            self._save_state(rev, sha256)
        except OSError:
            pass

    def _read_local(self) -> bytes:
        # Оставшийся журнал означает незавершённую запись: файл базы без него
        # несогласован, выкладывать такой нельзя.
        if os.path.exists(self.db_path + "-journal"):
            raise SyncError("База занята незавершённой записью. Перезапустите программу.")
        with open(self.db_path, "rb") as f:
            return f.read()

    def _local_sha(self) -> str:
        if not os.path.exists(self.db_path):
            return ""
        return hashlib.sha256(self._read_local()).hexdigest()

    def _local_is_empty(self) -> bool:
        """В базе нет ни клиентов, ни импортов — терять на этом устройстве нечего."""
        if not os.path.exists(self.db_path):
            return True
        conn = sqlite3.connect(self.db_path)
        try:
            for table in ("clients", "import_sessions", "shipments"):
                try:
                    if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                        return False
                except sqlite3.OperationalError:
                    continue  # таблицы ещё нет
            return True
        except sqlite3.DatabaseError as e:
            raise SyncError(f"Файл базы на этом устройстве повреждён: {e}") from e
        finally:
            conn.close()

    def _local_differs_from_base(self, local_sha: str, base_sha: str) -> bool:
        """Есть ли в локальной базе правки относительно последней синхронизации.
        Пустая база правкой не считается: выкладывать её поверх Диска незачем."""
        return local_sha != base_sha and not self._local_is_empty()

    def remote_meta(self) -> Optional[RemoteMeta]:
        raw = self.client.get(f"{self.remote_dir}/{META_NAME}", max_bytes=_MAX_META_BYTES)
        if raw is None:
            return None
        try:
            return _parse_meta(raw)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise SyncError(f"Файл {META_NAME} на Диске повреждён.") from e

    # --- операции ---

    def local_changed(self) -> bool:
        """Есть ли невыложенные локальные правки. Без обращения к сети."""
        return self._local_differs_from_base(self._local_sha(), self._load_state()["base_sha256"])

    def check(self) -> SyncCheck:
        """Сравнить локальную базу с Диском. Если содержимое совпало, запоминает
        ревизию Диска как базовую (так восстанавливается потерянное состояние)."""
        meta = self.remote_meta()
        state = self._load_state()
        local_sha = self._local_sha()
        local_changed = self._local_differs_from_base(local_sha, state["base_sha256"])

        if meta is None:
            if state["base_rev"] > 0 and not self._local_is_empty():
                return SyncCheck(SyncStatus.REMOTE_MISSING, None)
            status = SyncStatus.LOCAL_AHEAD if local_changed else SyncStatus.UP_TO_DATE
            return SyncCheck(status, None)

        if local_sha == meta.sha256:
            if state["base_rev"] != meta.rev or state["base_sha256"] != meta.sha256:
                self._remember(meta.rev, meta.sha256)
            return SyncCheck(SyncStatus.UP_TO_DATE, meta)

        # Сравниваем и содержимое, а не только номер: папку на Диске могли
        # пересоздать, и тот же номер ревизии достался бы другой базе.
        remote_changed = meta.rev != state["base_rev"] or meta.sha256 != state["base_sha256"]
        if remote_changed and local_changed:
            status = SyncStatus.CONFLICT
        elif remote_changed or not local_changed:
            # Вторая ветка — пустая локальная база при непустом Диске: её не
            # выкладываем, а заменяем версией с Диска.
            status = SyncStatus.REMOTE_AHEAD
        else:
            status = SyncStatus.LOCAL_AHEAD
        rewound = meta.rev < state["base_rev"] or (
            state["base_rev"] > 0 and meta.rev == state["base_rev"]
            and meta.sha256 != state["base_sha256"]
        )
        return SyncCheck(status, meta, remote_is_older=rewound)

    def push(self, expected: Optional[RemoteMeta], token: Optional[CancelToken] = None) -> int:
        """Выложить локальную базу новой ревизией. Возвращает номер ревизии.

        expected — ревизия Диска, по которой принималось решение (SyncCheck.meta).
        Если Диск с тех пор изменился, выгрузка отменяется: иначе чужая свежая
        ревизия была бы молча перекрыта. Соединения с базой должны быть закрыты.
        """
        raw = self._read_local()
        sha = hashlib.sha256(raw).hexdigest()
        # Повреждённую базу не выкладываем: она сломала бы второе устройство.
        _validate_database(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()

        # От максимума: после отката Диска номер не должен оказаться меньше уже
        # виденного другими устройствами.
        rev = max(expected.rev if expected else 0, self._load_state()["base_rev"]) + 1
        if rev > _MAX_REV:
            raise SyncError("Исчерпаны номера ревизий на Диске.")
        name = f"ozon_sorter_rev{rev:06d}_{sha[:8]}.db.gz"

        self.client.ensure_dir(self.remote_dir)
        # Сначала данные, потом meta.json: оборванная выгрузка оставит на Диске
        # лишний файл, но текущая ревизия останется прежней и целой.
        self.client.put(f"{self.remote_dir}/{name}", gzip.compress(raw, 6),
                        timeout=self.transfer_timeout)
        (token or CancelToken()).commit()
        if not _same_revision(self.remote_meta(), expected):
            raise SyncError(
                "Пока шла выгрузка, базу на Диске изменило другое устройство. "
                "Повторите синхронизацию."
            )
        new_meta = {
            "rev": rev, "file": name, "sha256": sha, "schema_version": schema_version,
            "device": self.device_name,
            "saved_at": datetime.now().strftime(_SAVED_AT_FORMAT),
        }
        self.client.put(f"{self.remote_dir}/{META_NAME}",
                        json.dumps(new_meta, ensure_ascii=False).encode("utf-8"))
        self._remember(rev, sha)
        self._prune_remote(rev)
        return rev

    def _prune_remote(self, current_rev: int) -> None:
        """Оставить на Диске последние KEEP_REMOTE_REVISIONS ревизий."""
        try:
            for name in self.client.list_dir(self.remote_dir):
                match = _REVISION_RE.fullmatch(name)
                if match and int(match.group(1)) <= current_rev - KEEP_REMOTE_REVISIONS:
                    self.client.delete(f"{self.remote_dir}/{name}")
        except WebDavError:
            # Выгрузка уже состоялась; лишние старые файлы уберёт следующая.
            pass

    def pull(self, expected: Optional[RemoteMeta] = None,
             token: Optional[CancelToken] = None) -> int:
        """Заменить локальную базу версией с Диска. Возвращает номер ревизии.

        expected — ревизия, которую пользователю показали и которую он согласился
        загрузить; если на Диске уже другая, загрузка отменяется. Прежний
        локальный файл сохраняется в backups/presync/. Соединения с базой должны
        быть закрыты вызывающим.
        """
        meta = self.remote_meta()
        if meta is None:
            raise SyncError("На Диске ещё нет базы.")
        if expected is not None and not _same_revision(meta, expected):
            raise SyncError("База на Диске только что изменилась. Повторите синхронизацию.")
        if meta.schema_version > SCHEMA_VERSION:
            raise SyncError(
                "База на Диске создана более новой версией программы. "
                "Обновите программу на этом устройстве."
            )
        packed = self.client.get(f"{self.remote_dir}/{meta.file}",
                                 timeout=self.transfer_timeout, max_bytes=MAX_DB_BYTES)
        if packed is None:
            raise SyncError(f"На Диске нет файла базы, на который ссылается {META_NAME}.")
        raw = self._unpack(packed)
        # Контрольная сумма лежит там же, где файл, и защищает только от порчи при
        # передаче; содержимое проверяет _validate_database ниже.
        if hashlib.sha256(raw).hexdigest() != meta.sha256:
            raise SyncError("Файл базы на Диске не совпадает с контрольной суммой. Повторите позже.")

        tmp = self.db_path + ".sync-tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            _validate_database(tmp)
            (token or CancelToken()).commit()
            self._rollback = (self._backup_local(), self._load_state())
            self._replace_local(tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self._remember(meta.rev, meta.sha256)
        return meta.rev

    def rollback_pull(self) -> None:
        """Вернуть базу, которая была до последнего pull() (если загруженная
        не открылась в программе)."""
        if self._rollback is None:
            return
        backup_path, state = self._rollback
        if backup_path is None:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        else:
            tmp = self.db_path + ".sync-tmp"
            try:
                shutil.copy2(backup_path, tmp)
                self._replace_local(tmp)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        # Только после успешной замены: при сбое откат можно повторить.
        self._rollback = None
        self._save_state(state["base_rev"], state["base_sha256"])

    @staticmethod
    def _unpack(packed: bytes) -> bytes:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(packed)) as f:
                raw = f.read(MAX_DB_BYTES + 1)
        except (OSError, EOFError, zlib.error) as e:
            raise SyncError("Файл базы на Диске повреждён.") from e
        if len(raw) > MAX_DB_BYTES:
            raise SyncError("Файл базы на Диске слишком большой и не может быть загружен.")
        return raw

    def _backup_local(self) -> Optional[str]:
        """Копия текущей базы перед заменой. Возвращает путь или None, если базы нет."""
        if not os.path.exists(self.db_path):
            return None
        # Отдельная папка: общая ротация backups/ (30 последних) не вытеснит копии,
        # сделанные перед заменой базы.
        backup_dir = os.path.join(os.path.dirname(self.db_path), "backups", "presync")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = os.path.join(backup_dir, f"ozon_sorter_{stamp}.db.bak")
        shutil.copy2(self.db_path, backup_path)
        for old in sorted(os.listdir(backup_dir))[:-KEEP_PRESYNC_BACKUPS]:
            os.remove(os.path.join(backup_dir, old))
        return backup_path

    def _replace_local(self, tmp: str) -> None:
        try:
            _replace_file(tmp, self.db_path)
        except PermissionError:
            raise SyncError("Файл базы занят другой программой. Повторите синхронизацию.")
