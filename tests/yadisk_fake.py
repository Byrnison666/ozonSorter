"""Локальный «Яндекс.Диск» для тестов: REST API, вход по коду устройства и
login.yandex.ru/info. Хранит файлы в памяти, слушает 127.0.0.1.

Повторяет то поведение, на которое опирается клиент: токен в заголовке
Authorization: OAuth; выгрузка и скачивание — по ссылке, которую выдаёт API
(на неё токен не передаётся, скачивание идёт через переадресацию); перенос и
копирование могут выполняться в фоне (202 + операция); 409 на существующую
папку и при отсутствии родителя. Пути — внутри app:/, ключи files — "/папка/имя".

Для сценариев отказов: offline (соединение обрывается без ответа), fail_next
(один раз вернуть код на HTTP-метод запроса к API), truncate_gets (скачивание
обещает больше байт, чем отдаёт), async_ops (перенос и копирование — через
фоновую операцию), auth_seen (Authorization запросов на выгрузку и скачивание),
download_location (куда переадресует ссылка на скачивание вместо хранилища).
"""
import itertools
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "/v1/disk"


class FakeYaDiskServer:
    def __init__(self, token="test-token", uid="1001", login="tester"):
        self.token = token
        self.uid = uid
        self.login = login
        self.files = {}          # "/dir/name" -> bytes
        self.dirs = {"/"}
        self.requests = []       # (method, путь запроса)
        self.fail_next = {}      # HTTP-метод -> код, который API вернёт один раз
        self.offline = False
        self.truncate_gets = False
        self.async_ops = False
        self.auth_seen = []      # Authorization запросов к ссылкам выгрузки/скачивания
        self.approved = False    # пользователь подтвердил код устройства
        self.device_interval = 1
        self.download_location = None
        self._uploads = {}       # id -> путь
        self._downloads = {}     # id -> путь
        self._operations = {}    # id -> сколько раз ещё ответить in-progress
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body=b"", content_type="application/json", headers=()):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers:
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # клиент сам оборвал приём (предел размера ответа)

            def _error(self, status, name):
                self._reply(status, {"error": name, "description": name})

            def _handle(self, method):
                url = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(url.query))
                server.requests.append((method, url.path))
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if server.offline:
                    self.close_connection = True
                    return  # без ответа: клиент увидит оборванное соединение
                with server._lock:
                    if url.path.startswith(API):
                        if self.headers.get("Authorization") != f"OAuth {server.token}":
                            return self._error(401, "UnauthorizedError")
                        forced = server.fail_next.pop(method, None)
                        if forced is not None:
                            return self._error(forced, "ForcedError")
                        return self._api(method, url.path[len(API):], query)
                    if url.path.startswith("/upload/") and method == "PUT":
                        server.auth_seen.append(self.headers.get("Authorization"))
                        return self._upload(url.path[len("/upload/"):], body)
                    if url.path.startswith("/download/") and method == "GET":
                        server.auth_seen.append(self.headers.get("Authorization"))
                        # Как у Яндекса: ссылка API переадресует на хранилище.
                        target = (server.download_location
                                  or "/storage/" + url.path[len("/download/"):])
                        return self._reply(302, headers=(("Location", target),))
                    if url.path.startswith("/storage/") and method == "GET":
                        server.auth_seen.append(self.headers.get("Authorization"))
                        return self._storage(url.path[len("/storage/"):])
                    if url.path.startswith("/oauth/") and method == "POST":
                        return self._oauth(url.path[len("/oauth/"):],
                                           dict(urllib.parse.parse_qsl(body.decode("ascii"))))
                    if url.path == "/info" and method == "GET":
                        if self.headers.get("Authorization") != f"OAuth {server.token}":
                            return self._error(401, "unauthorized")
                        return self._reply(200, {"id": server.uid, "login": server.login})
                return self._error(404, "NotFound")

            do_GET = lambda self: self._handle("GET")
            do_PUT = lambda self: self._handle("PUT")
            do_POST = lambda self: self._handle("POST")
            do_DELETE = lambda self: self._handle("DELETE")

            @staticmethod
            def _path(value):
                if not value or not value.startswith("app:/"):
                    return None
                return "/" + "/".join(s for s in value[len("app:/"):].split("/") if s)

            @staticmethod
            def _parent(path):
                return path.rsplit("/", 1)[0] or "/"

            def _operation(self, status=201):
                if not server.async_ops:
                    return self._reply(status, {"href": "", "method": "GET"})
                op = str(next(server._ids))
                server._operations[op] = 1
                href = f"{server.url}{API}/operations/{op}"
                return self._reply(202, {"href": href, "method": "GET", "templated": False})

            def _api(self, method, endpoint, query):
                path = self._path(query.get("path"))
                if endpoint.startswith("/operations/") and method == "GET":
                    op = endpoint[len("/operations/"):]
                    if op not in server._operations:
                        return self._error(404, "DiskNotFoundError")
                    left = server._operations[op]
                    server._operations[op] = max(0, left - 1)
                    return self._reply(200, {"status": "in-progress" if left else "success"})
                if path is None:
                    return self._error(400, "FieldValidationError")
                if endpoint == "/resources" and method == "GET":
                    if path in server.files:
                        return self._reply(200, {"name": path.rsplit("/", 1)[-1], "type": "file"})
                    if path not in server.dirs:
                        return self._error(404, "DiskNotFoundError")
                    items = [{"name": p.rsplit("/", 1)[-1]}
                             for p in sorted(list(server.files) + list(server.dirs))
                             if p != path and self._parent(p) == path]
                    return self._reply(200, {"_embedded": {"items": items}})
                if endpoint == "/resources" and method == "PUT":
                    if path in server.dirs or path in server.files:
                        return self._error(409, "DiskPathPointsToExistentDirectoryError")
                    if self._parent(path) not in server.dirs:
                        return self._error(409, "DiskPathDoesntExistsError")
                    server.dirs.add(path)
                    return self._reply(201, {"href": "", "method": "GET"})
                if endpoint == "/resources" and method == "DELETE":
                    if path in server.files:
                        del server.files[path]
                        return self._reply(204)
                    return self._error(404, "DiskNotFoundError")
                if endpoint == "/resources/upload" and method == "GET":
                    if self._parent(path) not in server.dirs:
                        return self._error(409, "DiskPathDoesntExistsError")
                    if path in server.files and query.get("overwrite") != "true":
                        return self._error(409, "DiskResourceAlreadyExistsError")
                    upload_id = str(next(server._ids))
                    server._uploads[upload_id] = path
                    return self._reply(200, {"operation_id": upload_id, "method": "PUT",
                                             "href": f"{server.url}/upload/{upload_id}",
                                             "templated": False})
                if endpoint == "/resources/download" and method == "GET":
                    if path not in server.files:
                        return self._error(404, "DiskNotFoundError")
                    download_id = str(next(server._ids))
                    server._downloads[download_id] = path
                    return self._reply(200, {"method": "GET", "templated": False,
                                             "href": f"{server.url}/download/{download_id}"})
                if endpoint in ("/resources/move", "/resources/copy") and method == "POST":
                    source = self._path(query.get("from"))
                    if source not in server.files:
                        return self._error(404, "DiskNotFoundError")
                    if self._parent(path) not in server.dirs:
                        return self._error(409, "DiskPathDoesntExistsError")
                    if path in server.files and query.get("overwrite") != "true":
                        return self._error(409, "DiskResourceAlreadyExistsError")
                    server.files[path] = server.files[source]
                    if endpoint == "/resources/move":
                        del server.files[source]
                    return self._operation()
                return self._error(405, "MethodNotAllowed")

            def _upload(self, upload_id, body):
                path = server._uploads.pop(upload_id, None)
                if path is None:
                    return self._error(404, "NotFound")
                server.files[path] = body
                return self._reply(201, b"", "text/plain")

            def _storage(self, download_id):
                path = server._downloads.get(download_id)
                if path is None or path not in server.files:
                    return self._error(404, "NotFound")
                data = server.files[path]
                if server.truncate_gets:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data) + 50))
                    self.end_headers()
                    self.wfile.write(data)
                    self.close_connection = True
                    return None
                return self._reply(200, data, "application/octet-stream")

            def _oauth(self, endpoint, form):
                if endpoint == "device/code":
                    return self._reply(200, {
                        "device_code": "device-code-1", "user_code": "TESTCODE",
                        "verification_url": "https://ya.ru/device",
                        "interval": server.device_interval, "expires_in": 300,
                    })
                if endpoint == "token" and form.get("grant_type") == "device_code":
                    if not server.approved:
                        return self._reply(400, {"error": "authorization_pending"})
                    return self._reply(200, {"access_token": server.token, "token_type": "bearer",
                                             "refresh_token": "refresh-1",
                                             "expires_in": 31536000})
                if endpoint == "token" and form.get("grant_type") == "refresh_token":
                    if form.get("refresh_token") != "refresh-1":
                        return self._reply(400, {"error": "invalid_grant"})
                    return self._reply(200, {"access_token": server.token, "token_type": "bearer",
                                             "refresh_token": "refresh-1",
                                             "expires_in": 31536000})
                return self._reply(400, {"error": "unsupported_grant_type"})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self._httpd.server_address[1]
        self.api_url = self.url + API
        self.oauth_url = self.url + "/oauth"
        self.info_url = self.url + "/info"
        # Короткий интервал опроса — иначе stop() ждёт до полсекунды на каждый тест.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join()
