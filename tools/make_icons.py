"""Иконки приложений из одного векторного знака (макет Figma «Иконки приложений»).

Запуск из корня проекта:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/make_icons.py

Пишет assets/icon.png, assets/icon.ico (ПК) и mobile/assets/*.png (телефон).
"""
import os
import struct
import sys

from PySide2.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide2.QtGui import QGuiApplication, QImage, QPainter
from PySide2.QtSvg import QSvgRenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS = 1024

# Знак — изометрическая коробка в системе координат 512: занимает x 128..384,
# y 120..416 (высота 296), центр (256, 268).
MARK = """
<polygon points="128,194 256,268 256,416 128,342" fill="#FFFFFF"/>
<polygon points="256,268 384,194 384,342 256,416" fill="#C3CDF2"/>
<polygon points="256,120 384,194 256,268 128,194" fill="url(#top)"/>
<polygon points="200.7,152 328.7,226 311.3,236 183.3,162" fill="#FFFFFF" fill-opacity="0.92"/>
"""
MARK_HEIGHT = 296
MARK_CENTER = (256, 268)

DEFS = f"""
<defs>
  <linearGradient id="top" gradientUnits="userSpaceOnUse" x1="0" y1="120" x2="0" y2="268">
    <stop offset="0" stop-color="#5A7DFF"/><stop offset="1" stop-color="#2F5BFF"/>
  </linearGradient>
  <linearGradient id="ink" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="{CANVAS}">
    <stop offset="0" stop-color="#1C2650"/><stop offset="1" stop-color="#0B1020"/>
  </linearGradient>
  <radialGradient id="glow" gradientUnits="userSpaceOnUse"
                  cx="{CANVAS / 2}" cy="{CANVAS * 0.45}" r="{CANVAS * 0.45}">
    <stop offset="0" stop-color="#2F5BFF" stop-opacity="0.5"/>
    <stop offset="1" stop-color="#2F5BFF" stop-opacity="0"/>
  </radialGradient>
</defs>
"""


def icon_svg(cube: float, radius: float, background: bool) -> str:
    """cube — доля высоты холста под коробку, radius — скругление фона в долях."""
    k = CANVAS * cube / MARK_HEIGHT
    dx = CANVAS / 2 - MARK_CENTER[0] * k
    dy = CANVAS / 2 - MARK_CENTER[1] * k
    bg = ""
    if background:
        r = CANVAS * radius
        bg = (
            f'<rect width="{CANVAS}" height="{CANVAS}" rx="{r}" ry="{r}" fill="url(#ink)"/>'
            f'<circle cx="{CANVAS / 2}" cy="{CANVAS * 0.45}" r="{CANVAS * 0.45}" fill="url(#glow)"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}" '
        f'viewBox="0 0 {CANVAS} {CANVAS}">{DEFS}{bg}'
        f'<g transform="translate({dx:.3f} {dy:.3f}) scale({k:.6f})">{MARK}</g></svg>'
    )


# Пропорции — как у мастер-компонентов в Figma.
WINDOWS = icon_svg(cube=0.64, radius=56 / 256, background=True)      # Windows не маскирует
SQUARE = icon_svg(cube=0.56, radius=0, background=True)              # маску даёт платформа
FOREGROUND = icon_svg(cube=0.42, radius=0, background=False)         # внутри safe-зоны 66 из 108dp
SPLASH = icon_svg(cube=0.86, radius=0, background=False)


def render(svg: str, size: int) -> QImage:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError("invalid SVG")
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    if not image.save(buf, "PNG"):
        raise RuntimeError("PNG encoding failed")
    return bytes(data)


def save_png(svg: str, size: int, path: str):
    with open(path, "wb") as f:
        f.write(png_bytes(render(svg, size)))
    print(f"{path} {size}px")


def save_ico(svg: str, sizes, path: str):
    """ICO из PNG-кадров: Windows Vista+ читает их во всех размерах. Каждый
    размер рендерится из вектора, а не уменьшается из 256 — мелкие чётче."""
    frames = [png_bytes(render(svg, s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries = b""
    for s, data in zip(sizes, frames):
        side = 0 if s >= 256 else s  # 0 в каталоге ICO означает 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(frames))
    print(f"{path} {list(sizes)}")


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 — QPainter без приложения не работает
    assets = os.path.join(ROOT, "assets")
    mobile = os.path.join(ROOT, "mobile", "assets")
    save_png(WINDOWS, 256, os.path.join(assets, "icon.png"))
    save_ico(WINDOWS, [16, 24, 32, 48, 64, 256], os.path.join(assets, "icon.ico"))
    save_png(SQUARE, 1024, os.path.join(mobile, "icon.png"))
    save_png(FOREGROUND, 1024, os.path.join(mobile, "adaptive-icon.png"))
    save_png(SPLASH, 1024, os.path.join(mobile, "splash-icon.png"))
    save_png(WINDOWS, 48, os.path.join(mobile, "favicon.png"))


if __name__ == "__main__":
    main()
