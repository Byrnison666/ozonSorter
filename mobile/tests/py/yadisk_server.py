"""Тестовый «Яндекс.Диск» ПК (tests/yadisk_fake.py) отдельным процессом.

Первая строка stdout — JSON {"url", "api_url", "oauth_url", "info_url", "control"}.
Управление — POST JSON на control: put/get/delete/list файлов «Диска», флаги
offline/server_error/fail_ops/truncate_gets/async_ops/approved/token, fail_next,
requests, auth_seen, clear.
Работает, пока открыт stdin.
"""
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from yadisk_fake import FakeYaDiskServer  # noqa: E402

dav = FakeYaDiskServer()


def handle(cmd):
    op = cmd["cmd"]
    if op == "put":
        path = cmd["path"]
        # Все папки по пути, как если бы их создал клиент.
        parts = path.split("/")[1:-1]
        for i in range(len(parts)):
            dav.dirs.add("/" + "/".join(parts[:i + 1]))
        dav.files[path] = base64.b64decode(cmd["b64"])
    elif op == "get":
        data = dav.files.get(cmd["path"])
        return {"b64": base64.b64encode(data).decode("ascii") if data is not None else None}
    elif op == "delete":
        dav.files.pop(cmd["path"], None)
    elif op == "list":
        return {"files": sorted(dav.files)}
    elif op == "set":
        for key in ("offline", "server_error", "fail_ops", "truncate_gets", "async_ops",
                    "approved", "token"):
            if key in cmd:
                setattr(dav, key, cmd[key])
    elif op == "fail_next":
        dav.fail_next[cmd["method"]] = cmd["code"]
    elif op == "auth_seen":
        return {"auth": list(dav.auth_seen)}
    elif op == "requests":
        return {"requests": [list(r) for r in dav.requests]}
    elif op == "clear":
        dav.files.clear()
        dav.dirs = {"/"}
        dav.requests.clear()
        dav.auth_seen.clear()
        dav.fail_next.clear()
        dav.server_error = None
        dav.fail_ops = False
        dav.approved = False
    else:
        raise ValueError(op)
    return {}


class Control(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.dumps(handle(json.loads(self.rfile.read(length)))).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


control = ThreadingHTTPServer(("127.0.0.1", 0), Control)
threading.Thread(target=control.serve_forever, daemon=True).start()
print(json.dumps({"url": dav.url, "api_url": dav.api_url, "oauth_url": dav.oauth_url,
                  "info_url": dav.info_url,
                  "control": "http://127.0.0.1:%d" % control.server_address[1]}), flush=True)
sys.stdin.read()  # до закрытия stdin тестом
control.shutdown()
dav.stop()
