"""Настройки синхронизации: токен Яндекса, аккаунт, имя устройства.

Токены на Windows хранятся зашифрованными через DPAPI (привязаны к учётной
записи Windows): папку с данными программы заказчик пересылает разработчику при
проблемах, и токен не должен уезжать вместе с ней в открытом виде.
"""
import base64
import hashlib
import json
import os
import platform
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

from .database import APP_DATA_DIR
from .yadisk_api import API_URL

CONFIG_PATH = os.path.join(APP_DATA_DIR, "sync.json")
STATE_PATH = os.path.join(APP_DATA_DIR, "sync_state.json")
# Папка синхронизации внутри app:/ (корень — «Приложения/OzonSorter»).
REMOTE_DIR = ""
# Версии до 1.9.0 работали по WebDAV с папкой OzonSorter в корне Диска.
_LEGACY_URL = "https://webdav.yandex.ru"
_LEGACY_REMOTE_DIR = "OzonSorter"


@dataclass
class SyncConfig:
    token: str = ""
    refresh_token: str = ""
    token_expires_at: int = 0          # unix-время; 0 — неизвестно
    uid: str = ""                      # аккаунт Яндекса, к которому выдан токен
    login: str = ""                    # для показа в настройках
    device_name: str = field(default_factory=lambda: platform.node() or "ПК")
    # Токен сохранён, но расшифровать его не удалось (другой компьютер или
    # учётная запись Windows): синхронизация выключена, пока не войдут заново.
    token_unreadable: bool = False
    # Не сохраняются: адрес API и папку меняют только тесты (через константы модуля).
    api_url: str = field(default_factory=lambda: API_URL)
    remote_dir: str = field(default_factory=lambda: REMOTE_DIR)
    # Настройки WebDAV-версии: по ним при первом входе через Яндекс узнаётся
    # старое состояние синхронизации (adopt_legacy_state).
    legacy_login: str = ""
    legacy_target_id: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.uid)

    @property
    def target_id(self) -> str:
        """Куда синхронизируемся: API, аккаунт и папка одной строкой."""
        key = f"{self.api_url}|{self.uid}|{self.remote_dir.strip('/')}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _legacy_target_id(url: str, login: str, remote_dir: str) -> str:
    """target_id из настроек WebDAV-версии — тем же способом, что она считала."""
    key = f"{url}|{login}|{remote_dir.strip('/')}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _normalize_login(login: str) -> str:
    login = login.strip().lower()
    for domain in ("@yandex.ru", "@ya.ru"):
        if login.endswith(domain):
            login = login[:-len(domain)]
    # Яндекс считает точку и дефис в логине одним и тем же символом.
    return login.replace(".", "-")


def adopt_legacy_state(config: SyncConfig, state_path: Optional[str] = None) -> bool:
    """После первого входа через Яндекс: если раньше это устройство
    синхронизировалось по WebDAV тем же аккаунтом, перенести состояние
    синхронизации на новое место (базу на Диске переносят в папку приложения
    заранее). Иначе устройство считалось бы ни разу не синхронизированным.

    Другой аккаунт — состояние не переносится: чужая база не должна выглядеть
    продолжением этой. True — состояние перенесено.
    """
    state_path = state_path or STATE_PATH
    if not (config.legacy_target_id and config.legacy_login and config.login):
        return False
    if _normalize_login(config.legacy_login) != _normalize_login(config.login):
        return False
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict) or state.get("target") != config.legacy_target_id:
            return False
        state["target"] = config.target_id
        tmp = f"{state_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, state_path)
    except (OSError, ValueError):
        return False
    return True


def forget_legacy(config: SyncConfig) -> None:
    """Настройки WebDAV-версии больше не нужны: вход через Яндекс выполнен."""
    config.legacy_login = ""
    config.legacy_target_id = ""


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    # Без явного типа указатель на 64-битной системе обрезался бы до 32 бит.
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not call(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _protect(secret: str):
    if not secret:
        # Пустую строку шифровать незачем; иначе при чтении она была бы принята за
        # «сохранена, но не расшифровывается».
        return "", "none"
    raw = secret.encode("utf-8")
    if sys.platform == "win32":
        return base64.b64encode(_dpapi(raw, True)).decode("ascii"), "dpapi"
    return base64.b64encode(raw).decode("ascii"), "none"


def _unprotect(stored: str, protection: str) -> str:
    try:
        raw = base64.b64decode(stored)
        if protection == "dpapi":
            if sys.platform != "win32":
                return ""
            raw = _dpapi(raw, False)
        return raw.decode("utf-8")
    except (OSError, ValueError):
        # Файл перенесли с другого компьютера или учётной записи — токен
        # расшифровать нельзя, придётся войти заново.
        return ""


def load_config(path: Optional[str] = None) -> SyncConfig:
    path = path or CONFIG_PATH
    config = SyncConfig()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return config
    except (OSError, ValueError):
        return config
    config.device_name = str(data.get("device_name") or config.device_name)
    protection = str(data.get("protection") or "none")
    stored_token = str(data.get("token") or "")
    config.token = _unprotect(stored_token, protection)
    config.refresh_token = _unprotect(str(data.get("refresh_token") or ""), protection)
    expires_at = data.get("token_expires_at")
    config.token_expires_at = expires_at if type(expires_at) is int else 0
    config.uid = str(data.get("uid") or "")
    config.login = str(data.get("login") or "")
    config.token_unreadable = bool(stored_token) and not config.token
    config.legacy_login = str(data.get("legacy_login") or "")
    config.legacy_target_id = str(data.get("legacy_target_id") or "")
    if "password" in data:
        # Файл WebDAV-версии: запоминаем, откуда она синхронизировалась.
        legacy_login = str(data.get("login") or "")
        config.login = ""
        if legacy_login:
            config.legacy_login = legacy_login
            config.legacy_target_id = _legacy_target_id(
                str(data.get("url") or _LEGACY_URL), legacy_login,
                str(data.get("remote_dir") or _LEGACY_REMOTE_DIR))
    return config


def save_config(config: SyncConfig, path: Optional[str] = None) -> None:
    path = path or CONFIG_PATH
    token, protection = _protect(config.token)
    refresh, _ = _protect(config.refresh_token)
    if not token and refresh:
        # Защита общая на оба поля; без access-токена refresh бесполезен.
        refresh = ""
    data = {
        "device_name": config.device_name, "uid": config.uid, "login": config.login,
        "token": token, "refresh_token": refresh, "protection": protection,
        "token_expires_at": config.token_expires_at,
    }
    if config.legacy_target_id:
        data["legacy_login"] = config.legacy_login
        data["legacy_target_id"] = config.legacy_target_id
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    # Файл создаём сразу с правами владельца: на Linux (dev) токен не зашифрован.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
