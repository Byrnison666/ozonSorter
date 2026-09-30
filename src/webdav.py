"""Минимальный WebDAV-клиент для синхронизации базы (Яндекс.Диск и совместимые).

Только то, что нужно синхронизации: папка, список, выгрузка, скачивание,
удаление. Без сторонних библиотек — urllib из стандартной поставки.
"""
import base64
import http.client
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# Предел размера ответа, если вызывающий не задал свой: служебные ответы малы,
# а сервер (или тот, кто им притворился) не должен мочь занять всю память.
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024


class WebDavError(Exception):
    """Ошибка обмена с сервером. status — код HTTP, None при сбое соединения."""

    def __init__(self, message: str, status: Optional[int] = None, tls: bool = False):
        super().__init__(message)
        self.status = status
        self.is_tls_error = tls

    @property
    def is_auth_error(self) -> bool:
        return self.status in (401, 403)

    @property
    def is_network_error(self) -> bool:
        """Нет связи (интернет выключен, сервер недоступен). Ошибка проверки
        сертификата сюда не входит: повтор «когда появится интернет» её не лечит."""
        return self.status is None and not self.is_tls_error


def _is_secure(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    )


def _check_complete(headers, body: bytes, max_bytes: int, method: str) -> None:
    """HTTPResponse.read(n) молча отдаёт меньше, если соединение оборвалось:
    сверяем с Content-Length, иначе обрыв выглядел бы как «файл повреждён»."""
    declared = headers.get("Content-Length") if headers is not None else None
    if declared and declared.isdigit() and len(body) <= max_bytes and len(body) < int(declared):
        raise WebDavError(f"Network error on {method}: response was cut off")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Переадресацию на незащищённый адрес не выполняем. Логин и пароль при
    переадресации не передаются вовсе: они добавлены как unredirected-заголовок."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_secure(newurl):
            raise urllib.error.HTTPError(
                newurl, code, "Redirect to insecure URL refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WebDavClient:
    def __init__(self, base_url: str, login: str, password: str, timeout: float = 30):
        # Логин и пароль уходят в каждом запросе (Basic) — без TLS их видно в сети.
        # Исключение — локальный адрес, на нём работает тестовый сервер.
        if not _is_secure(base_url):
            raise ValueError("WebDAV URL must use https")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        token = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
        self._auth_header = f"Basic {token}"
        self._opener = urllib.request.build_opener(_SafeRedirectHandler)

    def _url(self, path: str) -> str:
        segments = [urllib.parse.quote(s, safe="") for s in path.strip("/").split("/") if s]
        return self.base_url + "/" + "/".join(segments)

    def _request(self, method: str, path: str, data: Optional[bytes] = None,
                 headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None,
                 max_bytes: int = _DEFAULT_MAX_BYTES) -> Tuple[int, bytes]:
        request = urllib.request.Request(self._url(path), data=data, method=method)
        # urllib копирует обычные заголовки в запрос после переадресации, в том
        # числе на другой хост. Этот — нет.
        request.add_unredirected_header("Authorization", self._auth_header)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            try:
                with self._opener.open(request, timeout=timeout or self.timeout) as response:
                    status = response.status
                    body = response.read(max_bytes + 1)
                    _check_complete(response.headers, body, max_bytes, method)
            except urllib.error.HTTPError as e:
                # Ответ с кодом ошибки — тоже ответ: код разбирает вызывающий.
                status = e.code
                try:
                    body = e.read(max_bytes + 1) if e.fp is not None else b""
                    _check_complete(e.headers, body, max_bytes, method)
                finally:
                    e.close()
        except urllib.error.URLError as e:
            # Только отказ проверить сертификат — проблема доверия; обрыв связи в
            # рукопожатии или посреди передачи (SSLEOFError и т.п.) лечится повтором.
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                raise WebDavError(f"TLS error on {method}: {e.reason}", tls=True) from e
            raise WebDavError(f"Network error on {method}: {e.reason}") from e
        except ssl.SSLCertVerificationError as e:
            raise WebDavError(f"TLS error on {method}: {e}", tls=True) from e
        except (OSError, http.client.HTTPException) as e:
            raise WebDavError(f"Network error on {method}: {e}") from e
        if len(body) > max_bytes:
            raise WebDavError(f"{method} response is larger than {max_bytes} bytes")
        return status, body

    def _fail(self, method: str, path: str, status: int) -> WebDavError:
        return WebDavError(f"{method} {path} failed with HTTP {status}", status)

    def ensure_dir(self, path: str) -> None:
        """Создать папку со всеми промежуточными. Существующая — не ошибка."""
        current = ""
        for segment in [s for s in path.strip("/").split("/") if s]:
            current = f"{current}/{segment}"
            status, _ = self._request("MKCOL", current)
            # 405 — папка уже есть.
            if status not in (201, 405):
                raise self._fail("MKCOL", current, status)

    def list_dir(self, path: str) -> List[str]:
        """Имена в папке (без самой папки). Отсутствующая папка — пустой список."""
        status, body = self._request("PROPFIND", path, headers={"Depth": "1"})
        if status == 404:
            return []
        if status != 207:
            raise self._fail("PROPFIND", path, status)
        own = urllib.parse.unquote(urllib.parse.urlsplit(self._url(path)).path).rstrip("/")
        names = []
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise WebDavError(f"Malformed PROPFIND response: {e}", status) from e
        for href in root.iter("{DAV:}href"):
            entry = urllib.parse.unquote(urllib.parse.urlsplit(href.text or "").path).rstrip("/")
            if entry and entry != own:
                names.append(entry.rsplit("/", 1)[-1])
        return names

    def put(self, path: str, data: bytes, timeout: Optional[float] = None) -> None:
        status, _ = self._request(
            "PUT", path, data=data, timeout=timeout,
            headers={"Content-Type": "application/octet-stream"},
        )
        if status not in (200, 201, 204):
            raise self._fail("PUT", path, status)

    def get(self, path: str, timeout: Optional[float] = None,
            max_bytes: int = _DEFAULT_MAX_BYTES) -> Optional[bytes]:
        """Содержимое файла; None, если файла нет."""
        status, body = self._request("GET", path, timeout=timeout, max_bytes=max_bytes)
        if status == 404:
            return None
        if status != 200:
            raise self._fail("GET", path, status)
        return body

    def delete(self, path: str) -> None:
        """Удалить файл. Отсутствующий — не ошибка."""
        status, _ = self._request("DELETE", path)
        # 202 — Яндекс принимает удаление в фоновую обработку.
        if status not in (200, 202, 204, 404):
            raise self._fail("DELETE", path, status)
