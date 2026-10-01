"""Тестовый WebDAV-сервер ПК (tests/webdav_fake.py) отдельным процессом.

Первая строка stdout — JSON {"url", "control"}. Управление — POST JSON на
control: put/get/delete/list файлов «Диска», флаги offline/truncate_gets,
fail_next, clear. Работает, пока открыт stdin.
"""
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from webdav_fake import FakeWebDavServer  # noqa: E402

dav = FakeWebDavServer()


def handle(cmd):
    op = cmd["cmd"]
    if op == "put":
        path = cmd["path"]
        dav.dirs.add(path.rsplit("/", 1)[0] or "/")
        dav.files[path] = base64.b64decode(cmd["b64"])
    elif op == "get":
        data = dav.files.get(cmd["path"])
        return {"b64": base64.b64encode(data).decode("ascii") if data is not None else None}
    elif op == "delete":
        dav.files.pop(cmd["path"], None)
    elif op == "list":
        return {"files": sorted(dav.files)}
    elif op == "set":
        for key in ("offline", "truncate_gets"):
            if key in cmd:
                setattr(dav, key, cmd[key])
    elif op == "fail_next":
        dav.fail_next[cmd["method"]] = cmd["code"]
    elif op == "clear":
        dav.files.clear()
        dav.dirs = {"/"}
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
print(json.dumps({"url": dav.url, "control": "http://127.0.0.1:%d" % control.server_address[1]}),
      flush=True)
sys.stdin.read()  # до закрытия stdin тестом
control.shutdown()
dav.stop()
