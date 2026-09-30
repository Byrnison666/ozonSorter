"""Настройки синхронизации: адрес WebDAV, логин, пароль приложения, папка.

Пароль на Windows хранится зашифрованным через DPAPI (привязан к учётной записи
Windows): папку с данными программы заказчик пересылает разработчику при
проблемах, и пароль не должен уезжать вместе с ней в открытом виде.
"""
import base64
import hashlib
import json
import os
import platform
import sys
from dataclasses import dataclass, field
from typing import Optional

from .database import APP_DATA_DIR

CONFIG_PATH = os.path.join(APP_DATA_DIR, "sync.json")
STATE_PATH = os.path.join(APP_DATA_DIR, "sync_state.json")
DEFAULT_URL = "https://webdav.yandex.ru"
DEFAULT_REMOTE_DIR = "OzonSorter"


@dataclass
class SyncConfig:
    url: str = DEFAULT_URL
    login: str = ""
    password: str = ""
    remote_dir: str = DEFAULT_REMOTE_DIR
    device_name: str = field(default_factory=lambda: platform.node() or "ПК")
    # Пароль сохранён, но расшифровать его не удалось (другой компьютер или
    # учётная запись Windows): синхронизация выключена, пока его не введут заново.
    password_unreadable: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.login and self.password)

    @property
    def target_id(self) -> str:
        """Куда синхронизируемся: сервер, аккаунт и папка одной строкой."""
        key = f"{self.url}|{self.login}|{self.remote_dir.strip('/')}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


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


def _protect(password: str):
    if not password:
        # Пустой пароль шифровать незачем; иначе при чтении он был бы принят за
        # «сохранён, но не расшифровывается».
        return "", "none"
    raw = password.encode("utf-8")
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
        # Файл перенесли с другого компьютера или учётной записи — пароль
        # расшифровать нельзя, его придётся ввести заново.
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
    config.url = str(data.get("url") or DEFAULT_URL)
    config.login = str(data.get("login") or "")
    config.remote_dir = str(data.get("remote_dir") or DEFAULT_REMOTE_DIR)
    config.device_name = str(data.get("device_name") or config.device_name)
    stored = str(data.get("password") or "")
    config.password = _unprotect(stored, str(data.get("password_protection") or "none"))
    config.password_unreadable = bool(stored) and not config.password
    return config


def save_config(config: SyncConfig, path: Optional[str] = None) -> None:
    path = path or CONFIG_PATH
    stored, protection = _protect(config.password)
    data = {
        "url": config.url, "login": config.login, "remote_dir": config.remote_dir,
        "device_name": config.device_name,
        "password": stored, "password_protection": protection,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    # Файл создаём сразу с правами владельца: на Linux (dev) пароль не зашифрован.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
