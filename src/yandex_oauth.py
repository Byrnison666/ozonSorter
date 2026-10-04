"""Вход в Яндекс по коду устройства (OAuth device flow).

Программа показывает короткий код, пользователь вводит его на ya.ru/device в
любом браузере, где вошёл в Яндекс, — пароль от Яндекса программа не видит.
Токен даёт доступ только к папке приложения на Диске.

Секрет приложения в публичный репозиторий не попадает: он лежит в
src/_yandex_secret.py (в .gitignore) и попадает только в сборку.
"""
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Tuple

from .yadisk_api import Response, YaDiskError, http_request

OAUTH_URL = "https://oauth.yandex.ru"
INFO_URL = "https://login.yandex.ru/info"
CLIENT_ID = "23f4d228c2c940d1a2d16a94764029a0"
SCOPE = "cloud_api:disk.app_folder"
_TIMEOUT = 20


class OAuthError(Exception):
    """Вход не удался; code — код ошибки Яндекса (expired_token, access_denied…)."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_url: str
    interval: int
    expires_at: float          # time.monotonic()


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: int            # unix-время окончания действия access_token


def _client_secret() -> str:
    try:
        from . import _yandex_secret
    except ImportError:
        raise OAuthError("This build has no Yandex app secret", "not_configured")
    return _yandex_secret.CLIENT_SECRET


def _post(url: str, fields: dict) -> Response:
    body = urllib.parse.urlencode(fields).encode("ascii")
    return http_request("POST", url, {"Content-Type": "application/x-www-form-urlencoded",
                                      "Accept": "application/json"},
                        body, _TIMEOUT, 64 * 1024)


def _tokens(data: dict) -> Tokens:
    access, refresh, expires_in = (data.get("access_token"), data.get("refresh_token", ""),
                                   data.get("expires_in"))
    if not isinstance(access, str) or not access or not isinstance(refresh, str) \
            or type(expires_in) is not int:
        raise OAuthError("Malformed token response", "bad_response")
    return Tokens(access, refresh, int(time.time()) + expires_in)


def request_device_code(device_name: str) -> DeviceCode:
    response = _post(f"{OAUTH_URL}/device/code", {
        "client_id": CLIENT_ID, "device_name": device_name[:100] or "OzonSorter",
        "scope": SCOPE,
    })
    data = response.json()
    if response.status != 200:
        raise OAuthError(f"Device code request failed with HTTP {response.status}",
                         str(data.get("error", "")))
    fields = (data.get("device_code"), data.get("user_code"), data.get("verification_url"))
    interval, expires_in = data.get("interval", 5), data.get("expires_in")
    if not all(isinstance(f, str) and f for f in fields) or type(expires_in) is not int \
            or type(interval) is not int or not str(fields[2]).startswith("https://"):
        raise OAuthError("Malformed device code response", "bad_response")
    return DeviceCode(fields[0], fields[1], fields[2], max(1, min(interval, 30)),
                      time.monotonic() + expires_in)


def poll_token(code: DeviceCode) -> Optional[Tokens]:
    """Токен, если пользователь уже подтвердил код; None — ещё ждём."""
    if time.monotonic() > code.expires_at:
        raise OAuthError("Device code expired", "expired_token")
    response = _post(f"{OAUTH_URL}/token", {
        "grant_type": "device_code", "code": code.device_code,
        "client_id": CLIENT_ID, "client_secret": _client_secret(),
    })
    data = response.json()
    if response.status == 200:
        return _tokens(data)
    error = str(data.get("error", ""))
    if error in ("authorization_pending", "slow_down"):
        return None
    raise OAuthError(f"Token request failed with HTTP {response.status}", error)


def refresh_tokens(refresh_token: str) -> Tokens:
    response = _post(f"{OAUTH_URL}/token", {
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": CLIENT_ID, "client_secret": _client_secret(),
    })
    data = response.json()
    if response.status != 200:
        raise OAuthError(f"Token refresh failed with HTTP {response.status}",
                         str(data.get("error", "")))
    tokens = _tokens(data)
    # Яндекс может не прислать новый refresh_token — тогда действует прежний.
    return tokens if tokens.refresh_token else Tokens(tokens.access_token, refresh_token,
                                                      tokens.expires_at)


def fetch_user(access_token: str) -> Tuple[str, str]:
    """(uid, логин) владельца токена."""
    response = http_request("GET", f"{INFO_URL}?format=json",
                            {"Authorization": f"OAuth {access_token}"}, None, _TIMEOUT, 64 * 1024)
    if response.status != 200:
        raise YaDiskError(f"User info request failed with HTTP {response.status}",
                          response.status)
    data = response.json()
    uid, login = data.get("id"), data.get("login", "")
    if not isinstance(uid, str) or not uid.isdigit() or not isinstance(login, str):
        raise OAuthError("Malformed user info response", "bad_response")
    return uid, login
