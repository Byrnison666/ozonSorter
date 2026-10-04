"""Клиент REST API Яндекс.Диска для синхронизации базы.

Только то, что нужно синхронизации: папка, список, выгрузка, скачивание,
удаление, копирование. Все пути — внутри папки приложения (app:/ =
«Приложения/OzonSorter»): другого доступа у токена нет.

Почему не WebDAV: Яндекс искусственно замедляет выгрузку по WebDAV (около
минуты на мегабайт). REST API тоже замедляет файлы с «медийными» и архивными
расширениями (.gz в их числе), поэтому большой файл выгружается под временным
именем и переименовывается на сервере.

Без сторонних библиотек: http.client из стандартной поставки, чтобы видеть
ход передачи по кускам.
"""
import http.client
import json
import socket
import ssl
import time
import urllib.parse
from typing import Callable, Dict, List, Optional, Tuple

API_URL = "https://cloud-api.yandex.net/v1/disk"

# (передано байт, всего байт); всего 0 — размер неизвестен.
Progress = Callable[[int, int], None]

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_CHUNK = 64 * 1024
# Предел размера ответа, если вызывающий не задал свой: служебные ответы малы,
# а сервер (или тот, кто им притворился) не должен мочь занять всю память.
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_MAX_JSON_BYTES = 1024 * 1024
_MAX_REDIRECTS = 5
# Файлы не больше этого выгружаются сразу под своим именем: замедление по
# расширению на них не сказывается, а переименование — лишний запрос.
_DIRECT_UPLOAD_MAX = 64 * 1024
_TEMP_SUFFIX = ".tmp"
_POLL_DELAYS = (0.3, 0.5, 1.0, 1.0, 2.0)


class YaDiskError(Exception):
    """Ошибка обмена с Диском. status — код HTTP, None при сбое соединения."""

    def __init__(self, message: str, status: Optional[int] = None, tls: bool = False):
        super().__init__(message)
        self.status = status
        self.is_tls_error = tls

    @property
    def is_auth_error(self) -> bool:
        # 401 — токен отозван или истёк, 403 — у токена нет нужного права.
        return self.status in (401, 403)

    @property
    def is_network_error(self) -> bool:
        """Нет связи (интернет выключен, сервер недоступен). Ошибка проверки
        сертификата сюда не входит: повтор «когда появится интернет» её не лечит."""
        return self.status is None and not self.is_tls_error


def is_secure_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    )


def _origin(url: str) -> Tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.netloc.lower()


class Response:
    def __init__(self, status: int, headers: Dict[str, str], body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> dict:
        """Тело как JSON-объект; не JSON или не объект — пустой словарь."""
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def _connect(url: str, timeout: float):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname or "", parts.port, timeout=timeout,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(parts.hostname or "", parts.port, timeout=timeout)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return conn, target


def _exchange(method: str, url: str, headers: Dict[str, str], body: Optional[bytes],
              timeout: float, max_bytes: int, progress: Optional[Progress]) -> Response:
    conn, target = _connect(url, timeout)
    try:
        conn.putrequest(method, target, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body:
            view = memoryview(body)
            for start in range(0, len(body), _CHUNK):
                conn.send(view[start:start + _CHUNK])
                if progress:
                    progress(min(start + _CHUNK, len(body)), len(body))
        response = conn.getresponse()
        declared = response.getheader("Content-Length") or ""
        total = int(declared) if declared.isdigit() else 0
        if total > max_bytes:
            raise YaDiskError(f"{method} response is larger than {max_bytes} bytes",
                              response.status)
        received = bytearray()
        while True:
            chunk = response.read(min(_CHUNK, max_bytes + 1 - len(received)))
            if not chunk:
                break
            received += chunk
            if len(received) > max_bytes:
                raise YaDiskError(f"{method} response is larger than {max_bytes} bytes",
                                  response.status)
            if progress and body is None:
                progress(len(received), total)
        # read() молча отдаёт меньше, если соединение оборвалось: сверяем с
        # Content-Length, иначе обрыв выглядел бы как «файл повреждён».
        if total and len(received) < total:
            raise YaDiskError(f"Network error on {method}: response was cut off")
        response_headers = {k.lower(): v for k, v in response.getheaders()}
        return Response(response.status, response_headers, bytes(received))
    finally:
        conn.close()


def http_request(method: str, url: str, headers: Optional[Dict[str, str]] = None,
                 body: Optional[bytes] = None, timeout: float = 30,
                 max_bytes: int = _DEFAULT_MAX_BYTES, progress: Optional[Progress] = None,
                 follow_redirects: bool = False) -> Response:
    """Один HTTP-запрос. Ответ с кодом ошибки — тоже ответ: код разбирает
    вызывающий. Сбой связи и TLS — YaDiskError.

    Заголовки (в том числе токен) уходят только на исходный адрес: при
    переадресации их нет. Незащищённые адреса не открываются вовсе.
    """
    for _hop in range(_MAX_REDIRECTS + 1):
        if not is_secure_url(url):
            raise YaDiskError(f"Refusing insecure URL for {method}")
        try:
            response = _exchange(method, url, headers or {}, body, timeout, max_bytes, progress)
        except YaDiskError:
            raise
        except ssl.SSLCertVerificationError as e:
            # Только отказ проверить сертификат — проблема доверия; обрыв связи в
            # рукопожатии или посреди передачи (SSLEOFError и т.п.) лечится повтором.
            raise YaDiskError(f"TLS error on {method}: {e}", tls=True) from e
        except (OSError, http.client.HTTPException, socket.timeout) as e:
            raise YaDiskError(f"Network error on {method}: {e}") from e
        location = response.headers.get("location")
        if not (follow_redirects and response.status in (301, 302, 303, 307, 308) and location):
            return response
        url = urllib.parse.urljoin(url, location)
        headers = None
        if response.status == 303:
            method, body = "GET", None
    raise YaDiskError(f"Too many redirects on {method}")


class YaDiskClient:
    def __init__(self, token: str, api_url: str = API_URL, timeout: float = 30):
        # Токен уходит в каждом запросе — без TLS его видно в сети. Исключение —
        # локальный адрес, на нём работает тестовый сервер.
        if not is_secure_url(api_url):
            raise ValueError("Disk API URL must use https")
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    # --- служебное ---

    @staticmethod
    def _disk_path(path: str) -> str:
        return "app:/" + "/".join(s for s in path.split("/") if s)

    def _api(self, method: str, endpoint: str, params: Optional[Dict[str, str]] = None,
             url: Optional[str] = None) -> Response:
        if url is None:
            url = self.api_url + endpoint
            if params:
                url += "?" + urllib.parse.urlencode(params)
        # Токен — только своему API: адрес операции приходит в ответе сервера.
        if _origin(url) != _origin(self.api_url):
            raise YaDiskError(f"{method} {endpoint}: unexpected API host")
        headers = {"Authorization": f"OAuth {self.token}", "Accept": "application/json"}
        body = b"" if method in ("PUT", "POST") else None
        return http_request(method, url, headers, body, self.timeout, _MAX_JSON_BYTES)

    @staticmethod
    def _fail(method: str, what: str, response: Response) -> YaDiskError:
        error = response.json().get("error", "")
        return YaDiskError(f"{method} {what} failed with HTTP {response.status} {error}".rstrip(),
                           response.status)

    def _wait(self, href: str, timeout: Optional[float]) -> None:
        """Дождаться фоновой операции Диска (перенос, копирование)."""
        deadline = time.monotonic() + (timeout or self.timeout)
        for attempt in range(10_000):
            response = self._api("GET", "/operations", url=href)
            if response.status != 200:
                raise self._fail("GET", "operation", response)
            status = response.json().get("status")
            if status == "success":
                return
            if status != "in-progress":
                raise YaDiskError(f"Disk operation finished with status {status!r}", 500)
            if time.monotonic() > deadline:
                raise YaDiskError("Disk operation timed out")
            time.sleep(_POLL_DELAYS[min(attempt, len(_POLL_DELAYS) - 1)])

    def _finish(self, method: str, what: str, response: Response,
                timeout: Optional[float]) -> None:
        """201 — готово; 202 — Диск выполняет операцию в фоне, дожидаемся."""
        if response.status == 202:
            href = response.json().get("href")
            if not isinstance(href, str):
                raise self._fail(method, what, response)
            self._wait(href, timeout)
        elif response.status not in (200, 201):
            raise self._fail(method, what, response)

    def _upload(self, path: str, data: bytes, timeout: Optional[float],
                progress: Optional[Progress]) -> None:
        response = self._api("GET", "/resources/upload",
                             {"path": self._disk_path(path), "overwrite": "true"})
        if response.status != 200:
            raise self._fail("GET", f"upload link for {path}", response)
        link = response.json()
        href, method = link.get("href"), link.get("method") or "PUT"
        if not isinstance(href, str) or method not in ("PUT", "POST"):
            raise self._fail("GET", f"upload link for {path}", response)
        # Ссылка на выгрузку уже подписана — токен на чужой хост не отправляем.
        uploaded = http_request(method, href, {"Content-Type": "application/octet-stream"},
                                data, timeout or self.timeout, _MAX_JSON_BYTES, progress)
        if uploaded.status == 202 and isinstance(link.get("operation_id"), str):
            # Файл принят, но Диск ещё не положил его на место.
            op_url = f"{self.api_url}/operations/{urllib.parse.quote(link['operation_id'], safe='')}"
            self._wait(op_url, timeout)
        elif uploaded.status not in (200, 201, 202):
            raise self._fail(method, f"upload of {path}", uploaded)

    # --- операции ---

    def ensure_dir(self, path: str) -> None:
        """Создать папку со всеми промежуточными. Существующая — не ошибка."""
        current = ""
        for segment in [s for s in path.split("/") if s]:
            current = f"{current}/{segment}"
            response = self._api("PUT", "/resources", {"path": self._disk_path(current)})
            # 409 — папка уже есть.
            if response.status not in (201, 409):
                raise self._fail("PUT", current, response)

    def list_dir(self, path: str) -> List[str]:
        """Имена в папке. Отсутствующая папка — пустой список."""
        response = self._api("GET", "/resources", {
            "path": self._disk_path(path), "limit": "1000",
            "fields": "_embedded.items.name",
        })
        if response.status == 404:
            return []
        if response.status != 200:
            raise self._fail("GET", path, response)
        items = (response.json().get("_embedded") or {}).get("items") or []
        return [item["name"] for item in items
                if isinstance(item, dict) and isinstance(item.get("name"), str)]

    def put(self, path: str, data: bytes, timeout: Optional[float] = None,
            progress: Optional[Progress] = None) -> None:
        """Записать файл (существующий заменяется). Большой файл выгружается
        под временным именем и переименовывается: так обходится замедление по
        расширению, а оборванная выгрузка не оставит под настоящим именем
        недописанный файл."""
        if len(data) <= _DIRECT_UPLOAD_MAX:
            self._upload(path, data, timeout, progress)
            return
        temp = path + _TEMP_SUFFIX
        self._upload(temp, data, timeout, progress)
        response = self._api("POST", "/resources/move", {
            "from": self._disk_path(temp), "path": self._disk_path(path), "overwrite": "true",
        })
        self._finish("POST", f"move to {path}", response, timeout)

    def get(self, path: str, timeout: Optional[float] = None,
            max_bytes: int = _DEFAULT_MAX_BYTES,
            progress: Optional[Progress] = None) -> Optional[bytes]:
        """Содержимое файла; None, если файла нет."""
        response = self._api("GET", "/resources/download", {"path": self._disk_path(path)})
        if response.status == 404:
            return None
        href = response.json().get("href") if response.status == 200 else None
        if not isinstance(href, str):
            raise self._fail("GET", f"download link for {path}", response)
        # Ссылка на скачивание подписана и ведёт на другой хост — без токена.
        downloaded = http_request("GET", href, None, None, timeout or self.timeout,
                                  max_bytes, progress, follow_redirects=True)
        if downloaded.status == 404:
            return None
        if downloaded.status != 200:
            raise self._fail("GET", path, downloaded)
        return downloaded.body

    def copy(self, source: str, target: str, timeout: Optional[float] = None) -> None:
        """Копия файла на стороне Диска (без передачи по сети). Уже
        существующий target не заменяется и ошибкой не считается."""
        response = self._api("POST", "/resources/copy", {
            "from": self._disk_path(source), "path": self._disk_path(target),
            "overwrite": "false",
        })
        if response.status == 409:
            return
        self._finish("POST", f"copy to {target}", response, timeout)

    def delete(self, path: str) -> None:
        """Удалить файл в Корзину Диска. Отсутствующий — не ошибка. Фоновое
        удаление (202) не дожидаемся: на результат синхронизации оно не влияет."""
        response = self._api("DELETE", "/resources", {"path": self._disk_path(path)})
        if response.status not in (202, 204, 404):
            raise self._fail("DELETE", path, response)
