"""
Дизайн-система OzonSorter («Ink & Porcelain», макет в Figma).
Светлая контентная область + тёмный сайдбар. Акцент — синий, шрифт Onest.
"""
import os

from PySide2.QtCore import QByteArray, QEvent, QObject, QRectF, QSize, Qt
from PySide2.QtGui import QColor, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide2.QtSvg import QSvgRenderer
from PySide2.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

ASSETS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets")
)
_FONT_FILES = ("Onest-Regular.ttf", "Onest-Medium.ttf", "Onest-SemiBold.ttf", "Onest-Bold.ttf")


class Colors:
    # Базовые
    BG = "#F3F4F8"
    SURFACE = "#FFFFFF"
    SURFACE_ALT = "#F7F8FB"
    BORDER = "#E6E8F0"
    BORDER_STRONG = "#CED3DF"

    # Тёмные поверхности (сайдбар)
    INK = "#0B1020"
    INK_RAISED = "#171E38"
    INK_HOVER = "#121931"
    INK_LINE = "#232B47"
    INK_TEXT = "#8C94AE"
    INK_TEXT_STRONG = "#FFFFFF"
    INK_GROUP = "#545D7B"

    # Текст
    TEXT = "#0B1020"
    TEXT_SECONDARY = "#5A6379"
    TEXT_MUTED = "#9AA1B4"

    # Акценты
    PRIMARY = "#2F5BFF"
    PRIMARY_HOVER = "#2449E0"
    PRIMARY_SOFT = "#EDF1FF"
    PRIMARY_TEXT = "#1F3BB8"
    ON_PRIMARY = "#FFFFFF"

    SUCCESS = "#0F9F6E"
    SUCCESS_SOFT = "#E5F6EE"
    SUCCESS_TEXT = "#0A6A49"

    WARNING = "#D68A00"
    WARNING_SOFT = "#FFF4DA"
    WARNING_TEXT = "#8A5300"

    DANGER = "#E5484D"
    DANGER_SOFT = "#FDECEC"
    DANGER_TEXT = "#A5242A"


# Qt на Windows (GDI) видит Medium и SemiBold отдельными семействами — так они
# названы в таблице name шрифта. Поэтому начертание задаётся семейством, а не
# одним font-weight. Segoe UI — на случай, если шрифты не загрузились.
FONT_REGULAR = '"Onest", "Segoe UI"'
FONT_MEDIUM = '"Onest Medium", "Segoe UI"'
FONT_SEMIBOLD = '"Onest SemiBold", "Segoe UI"'


def load_fonts() -> bool:
    """Регистрирует Onest из assets/fonts. False — хотя бы один файл не загрузился."""
    ok = True
    for name in _FONT_FILES:
        path = os.path.join(ASSETS_DIR, "fonts", name)
        if QFontDatabase.addApplicationFont(path) == -1:
            print(f"Failed to load font: {path}")
            ok = False
    return ok


QSS = f"""
* {{
    font-family: {FONT_REGULAR};
    color: {Colors.TEXT};
}}

QMainWindow, QWidget#contentRoot {{
    background-color: {Colors.BG};
}}

QToolTip {{
    background-color: {Colors.INK_RAISED};
    color: {Colors.INK_TEXT_STRONG};
    border: none;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ---------- Sidebar ---------- */

QFrame#sidebar {{
    background-color: {Colors.INK};
    border: none;
}}

QLabel#brand {{
    color: {Colors.INK_TEXT_STRONG};
    font-family: {FONT_SEMIBOLD};
    font-size: 17px;
}}

QLabel#brandSub {{
    color: {Colors.INK_TEXT};
    font-size: 12px;
}}

QLabel#navGroup {{
    color: {Colors.INK_GROUP};
    font-family: {FONT_SEMIBOLD};
    font-size: 11px;
    padding: 18px 14px 6px 14px;
}}

QPushButton#navButton {{
    background: transparent;
    color: {Colors.INK_TEXT};
    border: none;
    border-radius: 8px;
    padding: 0 12px;
    min-height: 40px;
    max-height: 40px;
    font-family: {FONT_MEDIUM};
    font-size: 14px;
    text-align: left;
}}

QPushButton#navButton:hover {{
    background-color: {Colors.INK_HOVER};
    color: {Colors.INK_TEXT_STRONG};
}}

QPushButton#navButton:checked {{
    background-color: {Colors.INK_RAISED};
    color: {Colors.INK_TEXT_STRONG};
}}

QFrame#syncCard {{
    background-color: {Colors.INK_RAISED};
    border-radius: 12px;
}}

QLabel#syncIcon {{
    background-color: {Colors.INK_LINE};
    border-radius: 8px;
}}

QLabel#syncStatus {{
    color: {Colors.INK_TEXT};
    font-size: 12px;
}}

QLabel#syncStatusDirty {{
    color: {Colors.WARNING};
    font-family: {FONT_MEDIUM};
    font-size: 12px;
}}

QPushButton#syncButton {{
    background-color: {Colors.INK_LINE};
    color: {Colors.INK_TEXT_STRONG};
    border: none;
    border-radius: 10px;
    padding: 9px 14px;
    font-family: {FONT_SEMIBOLD};
}}

QPushButton#syncButton:hover {{
    background-color: #2C3555;
}}

QPushButton#syncButton:pressed {{
    background-color: {Colors.INK};
}}

QLabel#sidebarFooter {{
    color: {Colors.INK_GROUP};
    font-size: 11px;
    padding: 12px 8px 0 8px;
}}

/* ---------- Headings ---------- */

QLabel#h1 {{
    font-family: {FONT_SEMIBOLD};
    font-size: 26px;
    color: {Colors.TEXT};
}}

QLabel#h2 {{
    font-family: {FONT_SEMIBOLD};
    font-size: 17px;
    color: {Colors.TEXT};
}}

QLabel#subtitle {{
    font-size: 14px;
    color: {Colors.TEXT_SECONDARY};
}}

QLabel#muted {{
    font-size: 12px;
    color: {Colors.TEXT_MUTED};
}}

QLabel#overline, QLabel#sectionLabel {{
    font-family: {FONT_SEMIBOLD};
    font-size: 11px;
    color: {Colors.TEXT_MUTED};
}}

/* ---------- Cards ---------- */

QFrame#card {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 16px;
}}

QFrame#alertBanner {{
    background-color: {Colors.WARNING_SOFT};
    border: 1px solid {Colors.WARNING};
    border-radius: 12px;
}}

QFrame#alertBanner QPushButton {{
    border-color: {Colors.SURFACE};
}}

QLabel#alertIcon {{
    background-color: {Colors.WARNING};
    border-radius: 10px;
}}

QLabel#alertTitle {{
    font-family: {FONT_SEMIBOLD};
    font-size: 14px;
    color: {Colors.WARNING_TEXT};
}}

QLabel#alertText {{
    font-size: 12px;
    color: {Colors.WARNING_TEXT};
}}

QFrame#dropZone {{
    background-color: {Colors.SURFACE};
    border: 1px dashed #7E98FF;
    border-radius: 16px;
}}

QFrame#dropZone:hover {{
    border-color: {Colors.PRIMARY};
    background-color: #FAFBFF;
}}

QLabel#dropIcon {{
    background-color: {Colors.PRIMARY_SOFT};
    border-radius: 12px;
}}

QFrame#filePill {{
    background-color: {Colors.SURFACE_ALT};
    border: 1px solid {Colors.BORDER};
    border-radius: 14px;
}}

QFrame#filePill QLabel {{
    font-size: 12px;
    color: {Colors.TEXT_SECONDARY};
}}

/* ---------- Buttons ---------- */

QPushButton {{
    background-color: {Colors.SURFACE};
    color: {Colors.TEXT};
    border: 1px solid {Colors.BORDER_STRONG};
    border-radius: 10px;
    padding: 9px 16px;
    font-family: {FONT_MEDIUM};
    font-size: 14px;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {Colors.SURFACE_ALT};
    border-color: {Colors.TEXT_MUTED};
}}

QPushButton:pressed {{
    background-color: #ECEEF4;
}}

QPushButton:disabled {{
    color: {Colors.TEXT_MUTED};
    background-color: {Colors.SURFACE_ALT};
    border-color: {Colors.BORDER};
}}

QPushButton#secondary {{
    font-family: {FONT_SEMIBOLD};
}}

QPushButton#primary {{
    background-color: {Colors.PRIMARY};
    color: {Colors.ON_PRIMARY};
    border: 1px solid {Colors.PRIMARY};
    font-family: {FONT_SEMIBOLD};
    padding: 10px 18px;
}}

QPushButton#primary:hover {{
    background-color: {Colors.PRIMARY_HOVER};
    border-color: {Colors.PRIMARY_HOVER};
}}

QPushButton#primary:pressed {{
    background-color: {Colors.PRIMARY_TEXT};
    border-color: {Colors.PRIMARY_TEXT};
}}

QPushButton#primary:disabled {{
    background-color: #C3CDF2;
    border-color: #C3CDF2;
    color: {Colors.ON_PRIMARY};
}}

QPushButton#success {{
    background-color: {Colors.SUCCESS};
    color: {Colors.ON_PRIMARY};
    border: 1px solid {Colors.SUCCESS};
    font-family: {FONT_SEMIBOLD};
    padding: 10px 18px;
}}

QPushButton#success:hover {{
    background-color: {Colors.SUCCESS_TEXT};
    border-color: {Colors.SUCCESS_TEXT};
}}

QPushButton#danger {{
    background-color: {Colors.SURFACE};
    color: {Colors.DANGER_TEXT};
    border: 1px solid #F5C2C4;
}}

QPushButton#danger:hover {{
    background-color: {Colors.DANGER_SOFT};
    border-color: {Colors.DANGER};
}}

QPushButton#danger:pressed {{
    background-color: {Colors.DANGER};
    color: {Colors.ON_PRIMARY};
    border-color: {Colors.DANGER};
}}

QPushButton#chip {{
    background-color: {Colors.SURFACE};
    color: {Colors.TEXT};
    border: 1px solid {Colors.BORDER_STRONG};
    border-radius: 17px;
    padding: 0 14px;
    min-height: 32px;
    max-height: 32px;
}}

QPushButton#chip:hover {{
    border-color: {Colors.TEXT_MUTED};
}}

QPushButton#chip:checked {{
    background-color: {Colors.INK};
    color: {Colors.INK_TEXT_STRONG};
    border-color: {Colors.INK};
}}

/* ---------- Inputs ---------- */

QLineEdit, QComboBox {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER_STRONG};
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 14px;
    selection-background-color: {Colors.PRIMARY_SOFT};
    selection-color: {Colors.TEXT};
    color: {Colors.TEXT};
    min-height: 18px;
}}

QLineEdit:focus, QComboBox:focus {{
    border-color: {Colors.PRIMARY};
}}

QLineEdit:disabled, QComboBox:disabled {{
    background-color: {Colors.SURFACE_ALT};
    color: {Colors.TEXT_MUTED};
}}

QLineEdit#searchLarge {{
    font-size: 16px;
    padding: 12px 16px;
    border-radius: 12px;
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    selection-background-color: {Colors.PRIMARY_SOFT};
    selection-color: {Colors.TEXT};
    padding: 4px;
    outline: 0;
}}

/* ---------- Tables ---------- */

/* Таблицы лежат в карточках (QFrame#card) — своя рамка им не нужна. Фон
   прозрачный: прямоугольные углы таблицы иначе закрашивают скругление карточки. */
QTableWidget, QTableView {{
    background-color: transparent;
    border: none;
    gridline-color: transparent;
    font-size: 14px;
    selection-background-color: {Colors.PRIMARY_SOFT};
    selection-color: {Colors.TEXT};
    alternate-background-color: {Colors.SURFACE_ALT};
}}

QTableWidget::item, QTableView::item {{
    padding: 10px 12px;
    border: none;
    border-bottom: 1px solid #EEF0F5;
}}

QTableWidget::item:hover, QTableView::item:hover {{
    background-color: {Colors.SURFACE_ALT};
}}

QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {Colors.PRIMARY_SOFT};
    color: {Colors.TEXT};
}}

QHeaderView {{
    background-color: transparent;
}}

QHeaderView::section {{
    background-color: {Colors.SURFACE_ALT};
    color: {Colors.TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
    padding: 11px 12px;
    font-size: 12px;
}}

QHeaderView::section:first {{
    border-top-left-radius: 14px;
}}

QHeaderView::section:last {{
    border-top-right-radius: 14px;
}}

QTableCornerButton::section {{
    background-color: {Colors.SURFACE_ALT};
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
}}

/* ---------- Badges (status pills) ---------- */

QLabel#badgeInfo, QLabel#badgeWarning, QLabel#badgeSuccess,
QLabel#badgeNeutral, QLabel#badgeDanger {{
    border-radius: 11px;
    padding: 3px 10px;
    font-family: {FONT_MEDIUM};
    font-size: 12px;
}}

QLabel#badgeInfo {{
    background-color: {Colors.PRIMARY_SOFT};
    color: {Colors.PRIMARY_TEXT};
}}

QLabel#badgeWarning {{
    background-color: {Colors.WARNING_SOFT};
    color: {Colors.WARNING_TEXT};
}}

QLabel#badgeSuccess {{
    background-color: {Colors.SUCCESS_SOFT};
    color: {Colors.SUCCESS_TEXT};
}}

QLabel#badgeNeutral {{
    background-color: {Colors.SURFACE_ALT};
    color: {Colors.TEXT_SECONDARY};
}}

QLabel#badgeDanger {{
    background-color: {Colors.DANGER_SOFT};
    color: {Colors.DANGER_TEXT};
}}

/* ---------- Stat tiles ---------- */

QFrame#statTile {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 12px;
}}

QLabel#statLabel {{
    font-size: 12px;
    color: {Colors.TEXT_SECONDARY};
}}

QLabel#statValue {{
    font-family: {FONT_SEMIBOLD};
    font-size: 28px;
    color: {Colors.TEXT};
}}

QLabel#statDot {{
    background-color: {Colors.TEXT_MUTED};
    border-radius: 3px;
}}

QFrame#statTile[tone="warning"] {{
    background-color: {Colors.WARNING_SOFT};
    border-color: {Colors.WARNING_SOFT};
}}

QFrame#statTile[tone="warning"] QLabel#statLabel,
QFrame#statTile[tone="warning"] QLabel#statValue {{
    color: {Colors.WARNING_TEXT};
}}

QFrame#statTile[tone="warning"] QLabel#statDot {{
    background-color: {Colors.WARNING};
}}

QFrame#statTile[tone="danger"] {{
    background-color: {Colors.DANGER_SOFT};
    border-color: {Colors.DANGER_SOFT};
}}

QFrame#statTile[tone="danger"] QLabel#statLabel,
QFrame#statTile[tone="danger"] QLabel#statValue {{
    color: {Colors.DANGER_TEXT};
}}

QFrame#statTile[tone="danger"] QLabel#statDot {{
    background-color: {Colors.DANGER};
}}

QFrame#statTile[tone="hero"] {{
    background-color: {Colors.PRIMARY};
    border-color: {Colors.PRIMARY};
}}

QFrame#statTile[tone="hero"] QLabel#statLabel {{
    color: #DCE3FF;
}}

QFrame#statTile[tone="hero"] QLabel#statValue {{
    color: {Colors.ON_PRIMARY};
    font-size: 48px;
}}

QFrame#statTile[tone="hero"] QLabel#statDot {{
    background-color: {Colors.ON_PRIMARY};
}}

/* ---------- Scrollbars ---------- */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

/* Полоса прокрутки таблицы не заходит в скруглённые углы карточки. */
QTableView QScrollBar:vertical {{
    margin: 14px 0;
}}

QScrollBar::handle:vertical {{
    background: {Colors.BORDER_STRONG};
    border-radius: 3px;
    min-height: 30px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {Colors.TEXT_MUTED};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {Colors.BORDER_STRONG};
    border-radius: 3px;
    min-width: 30px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {Colors.TEXT_MUTED};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: transparent;
}}

/* ---------- Dialog ---------- */

QDialog {{
    background-color: {Colors.SURFACE};
}}

QDialog QLabel {{
    font-size: 14px;
}}

QMessageBox {{
    background-color: {Colors.SURFACE};
}}

QMessageBox QLabel {{
    font-size: 14px;
}}

/* ---------- Misc ---------- */

QFrame#hLine {{
    background-color: {Colors.BORDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

QFrame#vLine {{
    background-color: {Colors.BORDER};
    max-width: 1px;
    min-width: 1px;
    border: none;
}}
"""


def icon_pixmap(name: str, color: str, size: int, pad_right: int = 0) -> QPixmap:
    """Иконка Lucide из assets/icons нужного цвета (stroke="currentColor").
    pad_right — прозрачное поле справа от рисунка."""
    with open(os.path.join(ASSETS_DIR, "icons", f"{name}.svg"), encoding="utf-8") as f:
        svg = f.read().replace("currentColor", color)
    app = QGuiApplication.instance()
    ratio = app.devicePixelRatio() if app is not None else 1.0
    side = round(size * ratio)
    pixmap = QPixmap(round((size + pad_right) * ratio), side)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, side, side))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def set_button_icon(button: QPushButton, name: str, color: str, size: int = 16):
    """Иконка перед текстом кнопки. Qt ставит между ними всего 4px — поле
    справа от рисунка добавляет ещё 4, как gap 8 в макете."""
    pad = 4
    button.setIcon(QIcon(icon_pixmap(name, color, size, pad_right=pad)))
    button.setIconSize(QSize(size + pad, size))


def icon_label(name: str, color: str, size: int, box: int, object_name: str = "") -> QLabel:
    """Иконка по центру квадрата box×box (фон и скругление — из QSS по object_name)."""
    label = QLabel()
    if object_name:
        label.setObjectName(object_name)
    label.setFixedSize(box, box)
    label.setAlignment(Qt.AlignCenter)
    label.setPixmap(icon_pixmap(name, color, size))
    return label


def make_overline(text: str) -> QLabel:
    """Подпись группы капсом с разрядкой (стиль Overline в макете)."""
    label = QLabel(text.upper())
    label.setObjectName("overline")
    font = label.font()
    font.setLetterSpacing(font.PercentageSpacing, 108)
    label.setFont(font)
    return label


def make_stat_tile(label_text: str, initial_value: str = "0", tone: str = "neutral"):
    """Возвращает (frame, value_label) — плитка: подпись с точкой, под ней число.
    tone: neutral|warning|danger|hero (hero — главная цифра экрана, число внизу)."""
    frame = QFrame()
    frame.setObjectName("statTile")
    frame.setProperty("tone", tone)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(6)

    head = QHBoxLayout()
    head.setSpacing(6)
    dot = QLabel()
    dot.setObjectName("statDot")
    dot.setFixedSize(6, 6)
    head.addWidget(dot, 0, Qt.AlignVCenter)
    label = QLabel(label_text)
    label.setObjectName("statLabel")
    head.addWidget(label, 1)
    lay.addLayout(head)

    if tone == "hero":
        lay.addStretch(1)
    value = QLabel(initial_value)
    value.setObjectName("statValue")
    lay.addWidget(value)
    return frame, value


def make_badge(text: str, kind: str = "neutral"):
    """kind: neutral|info|success|warning|danger"""
    label = QLabel(text)
    object_name = {
        "info": "badgeInfo",
        "success": "badgeSuccess",
        "warning": "badgeWarning",
        "danger": "badgeDanger",
        "neutral": "badgeNeutral",
    }.get(kind, "badgeNeutral")
    label.setObjectName(object_name)
    return label


def hr():
    """Горизонтальная разделительная линия."""
    line = QFrame()
    line.setObjectName("hLine")
    return line


class _ShadowPlate(QWidget):
    """Пустая подложка под карточкой, которая отбрасывает тень.

    QGraphicsDropShadowEffect на самой карточке рисовал бы её содержимое через
    промежуточную картинку: текст теряет ClearType, а наведение на любую кнопку
    пересчитывает размытие всей карточки. Подложка без детей этого лишена."""

    _TRACKED = {
        QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide,
        QEvent.ShowToParent, QEvent.HideToParent, QEvent.ParentChange,
    }

    def __init__(self, card: QWidget, radius: int):
        super().__init__()
        self._card = card
        self._radius = radius
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(16)
        effect.setOffset(0, 4)
        effect.setColor(QColor(11, 16, 32, 15))  # ink, 6%
        self.setGraphicsEffect(effect)
        card.installEventFilter(self)
        self._sync()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._card and event.type() in self._TRACKED:
            self._sync()
        return False

    def _sync(self):
        parent = self._card.parentWidget()
        if parent is None:
            self.hide()
            return
        if self.parentWidget() is not parent:
            self.setParent(parent)
        self.setGeometry(self._card.geometry())
        self.stackUnder(self._card)
        self.setVisible(not self._card.isHidden())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(Colors.SURFACE))
        painter.drawRoundedRect(QRectF(self.rect()), self._radius, self._radius)


def add_shadow(card: QWidget, radius: int = 16):
    """Мягкая тень карточки (Elevation/Card в макете). Только для карточек без
    прокрутки: тень пересчитывается при каждом изменении размера."""
    card._shadow_plate = _ShadowPlate(card, radius)
