"""Локальный WebDAV-сервер для тестов: хранит файлы в памяти, слушает 127.0.0.1.

Повторяет ровно то поведение, на которое опирается клиент: Basic-авторизация,
MKCOL (405 на существующую папку, 409 без родителя), PUT/GET/DELETE, PROPFIND
с Depth: 1. Настоящий Яндекс.Диск в тестах не трогаем.

Для сценариев отказов: offline (соединение обрывается без ответа), redirects
(метод, путь) -> URL переадресации, auth_seen (заголовки Authorization каждого
запроса), fail_next (один раз вернуть заданный код на метод).
"""
import base64
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape


class FakeWebDavServer:
    def __init__(self, login="user", password="secret"):
        self.login = login
        self.password = password
        self.files = {}          # "/dir/name" -> bytes
        self.dirs = {"/"}
        self.requests = []       # (method, path) — для проверок порядка операций
        self.fail_next = {}      # method -> HTTP-код, который вернётся один раз
        self.offline = False     # True: соединения обрываются без ответа
        self.redirects = {}      # (method, path) -> URL для ответа 302
        self.auth_seen = []      # Authorization каждого полученного запроса
        self.truncate_gets = False   # True: GET обещает больше байт, чем отдаёт
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _path(self):
                return urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).rstrip("/") or "/"

            def _reply(self, status, body=b"", content_type="text/plain"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _begin(self, method):
                """Общие проверки. Возвращает путь или None, если ответ уже отправлен."""
                path = self._path()
                server.requests.append((method, path))
                server.auth_seen.append(self.headers.get("Authorization"))
                length = int(self.headers.get("Content-Length") or 0)
                self._body = self.rfile.read(length) if length else b""
                if server.offline:
                    self.close_connection = True
                    return None  # без ответа: клиент увидит оборванное соединение
                target = server.redirects.get((method, path))
                if target:
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                expected = "Basic " + base64.b64encode(
                    f"{server.login}:{server.password}".encode("utf-8")).decode("ascii")
                if self.headers.get("Authorization") != expected:
                    self._reply(401)
                    return None
                forced = server.fail_next.pop(method, None)
                if forced is not None:
                    self._reply(forced)
                    return None
                return path

            @staticmethod
            def _parent(path):
                return path.rsplit("/", 1)[0] or "/"

            def do_MKCOL(self):
                path = self._begin("MKCOL")
                if path is None:
                    return
                if path in server.dirs:
                    self._reply(405)
                elif self._parent(path) not in server.dirs:
                    self._reply(409)
                else:
                    server.dirs.add(path)
                    self._reply(201)

            def do_PUT(self):
                path = self._begin("PUT")
                if path is None:
                    return
                if self._parent(path) not in server.dirs:
                    self._reply(409)
                    return
                server.files[path] = self._body
                self._reply(201)

            def do_GET(self):
                path = self._begin("GET")
                if path is None:
                    return
                if path in server.files and server.truncate_gets:
                    body = server.files[path]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body) + 50))
                    self.end_headers()
                    self.wfile.write(body)
                    self.close_connection = True
                elif path in server.files:
                    self._reply(200, server.files[path], "application/octet-stream")
                else:
                    self._reply(404)

            def do_DELETE(self):
                path = self._begin("DELETE")
                if path is None:
                    return
                if path in server.files:
                    del server.files[path]
                    self._reply(204)
                else:
                    self._reply(404)

            def do_PROPFIND(self):
                path = self._begin("PROPFIND")
                if path is None:
                    return
                if path not in server.dirs:
                    self._reply(404)
                    return
                prefix = "" if path == "/" else path
                entries = [path] + sorted(
                    p for p in list(server.files) + list(server.dirs)
                    if p != path and self._parent(p) == path
                )
                items = "".join(
                    "<d:response><d:href>%s</d:href></d:response>"
                    % escape(urllib.parse.quote(e)) for e in entries
                )
                body = (
                    '<?xml version="1.0" encoding="utf-8"?>'
                    '<d:multistatus xmlns:d="DAV:">%s</d:multistatus>' % items
                ).encode("utf-8")
                self._reply(207, body, "application/xml")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self._httpd.server_address[1]
        # Короткий интервал опроса — иначе stop() ждёт до полсекунды на каждый тест.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join()
