"""Управление приложением на эмуляторе через adb: найти элемент по тексту в
дереве uiautomator, нажать, ввести текст, снять экран. Для сквозной проверки
(не входит в bun test: нужен запущенный эмулятор).

Использование как модуль или CLI:
  python adb_ui.py tap "Сохранить"
  python adb_ui.py wait "Клиенты" 20
  python adb_ui.py type "secret"
  python adb_ui.py shot /tmp/x.png
  python adb_ui.py texts
"""
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ADB = "/home/iorek/Android/Sdk/platform-tools/adb"


def adb(*args, check=True, capture=True):
    r = subprocess.run([ADB, *args], capture_output=capture, timeout=60)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {r.stderr.decode(errors='replace')}")
    return r.stdout


def nodes():
    """Все узлы текущего экрана: (text, content-desc, bounds-center)."""
    for _ in range(3):
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", check=False)
        raw = adb("exec-out", "cat", "/sdcard/ui.xml", check=False)
        if raw.strip().startswith(b"<?xml"):
            break
        time.sleep(0.5)
    else:
        return []
    out = []
    for n in ET.fromstring(raw).iter("node"):
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        out.append((n.get("text", ""), n.get("content-desc", ""), ((x1 + x2) // 2, (y1 + y2) // 2)))
    return out


def find(text, exact=False):
    for t, d, center in nodes():
        for value in (t, d):
            if value and (value == text if exact else text in value):
                return center
    return None


def wait(text, timeout=20.0, exact=False):
    end = time.time() + timeout
    while time.time() < end:
        c = find(text, exact)
        if c:
            return c
        time.sleep(0.7)
    raise TimeoutError(f"no element with text {text!r}; visible: {[n[0] or n[1] for n in nodes() if n[0] or n[1]]}")


def tap(text, timeout=20.0, exact=False):
    x, y = wait(text, timeout, exact)
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(0.6)


def type_text(value):
    # adb input text: пробелы — %s; кириллицу adb не вводит.
    adb("shell", "input", "text", value.replace(" ", "%s"))
    time.sleep(0.3)


def shot(path):
    with open(path, "wb") as f:
        f.write(adb("exec-out", "screencap", "-p"))


def texts():
    return [t or d for t, d, _ in nodes() if t or d]


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "tap":
        tap(rest[0], float(rest[1]) if len(rest) > 1 else 20.0)
    elif cmd == "wait":
        print(wait(rest[0], float(rest[1]) if len(rest) > 1 else 20.0))
    elif cmd == "type":
        type_text(rest[0])
    elif cmd == "shot":
        shot(rest[0])
    elif cmd == "texts":
        print("\n".join(texts()))
    else:
        raise SystemExit(f"unknown command {cmd}")
