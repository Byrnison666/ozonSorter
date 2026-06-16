"""
Дизайн-система OzonSorter.
Светлая контентная область + тёмный сайдбар. Акцент — синий.
"""


class Colors:
    # Базовые
    BG = "#F6F7FB"
    SURFACE = "#FFFFFF"
    SURFACE_ALT = "#F8FAFC"
    BORDER = "#E4E7EE"
    BORDER_STRONG = "#CBD5E1"

    # Сайдбар
    SIDEBAR = "#0F172A"
    SIDEBAR_HOVER = "#1E293B"
    SIDEBAR_GROUP = "#475569"
    SIDEBAR_TEXT = "#94A3B8"
    SIDEBAR_TEXT_HOVER = "#E2E8F0"
    SIDEBAR_TEXT_ACTIVE = "#FFFFFF"

    # Текст
    TEXT = "#0F172A"
    TEXT_SECONDARY = "#64748B"
    TEXT_MUTED = "#94A3B8"

    # Акценты
    PRIMARY = "#3B82F6"
    PRIMARY_HOVER = "#2563EB"
    PRIMARY_ACTIVE = "#1D4ED8"
    PRIMARY_SOFT = "#DBEAFE"
    PRIMARY_TEXT = "#1E40AF"

    SUCCESS = "#10B981"
    SUCCESS_HOVER = "#059669"
    SUCCESS_SOFT = "#D1FAE5"
    SUCCESS_TEXT = "#065F46"

    WARNING_SOFT = "#FEF3C7"
    WARNING_TEXT = "#92400E"

    DANGER = "#DC2626"
    DANGER_HOVER = "#B91C1C"
    DANGER_SOFT = "#FEE2E2"
    DANGER_TEXT = "#991B1B"

    DAMAGED_BG = "#FEE2E2"


QSS = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
    color: {Colors.TEXT};
}}

QMainWindow, QWidget#contentRoot {{
    background-color: {Colors.BG};
}}

QToolTip {{
    background-color: #1E293B;
    color: white;
    border: none;
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 12px;
}}

/* ---------- Sidebar ---------- */

QFrame#sidebar {{
    background-color: {Colors.SIDEBAR};
    border: none;
}}

QLabel#brand {{
    color: {Colors.SIDEBAR_TEXT_ACTIVE};
    font-size: 17px;
    font-weight: 700;
    padding: 22px 22px 4px 22px;
    letter-spacing: 0.3px;
}}

QLabel#brandSub {{
    color: {Colors.SIDEBAR_TEXT};
    font-size: 11px;
    padding: 0 22px 22px 22px;
    letter-spacing: 0.6px;
}}

QLabel#navGroup {{
    color: {Colors.SIDEBAR_GROUP};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 18px 22px 6px 22px;
}}

QPushButton#navButton {{
    background: transparent;
    color: {Colors.SIDEBAR_TEXT};
    border: none;
    border-left: 3px solid transparent;
    padding: 10px 22px 10px 19px;
    font-size: 13px;
    font-weight: 500;
    text-align: left;
    qproperty-iconSize: 18px;
}}

QPushButton#navButton:hover {{
    background-color: {Colors.SIDEBAR_HOVER};
    color: {Colors.SIDEBAR_TEXT_HOVER};
}}

QPushButton#navButton:checked {{
    background-color: {Colors.SIDEBAR_HOVER};
    color: {Colors.SIDEBAR_TEXT_ACTIVE};
    font-weight: 600;
    border-left: 3px solid {Colors.PRIMARY};
}}

QLabel#sidebarFooter {{
    color: {Colors.SIDEBAR_GROUP};
    font-size: 11px;
    padding: 16px 22px;
}}

/* ---------- Headings ---------- */

QLabel#h1 {{
    font-size: 24px;
    font-weight: 700;
    color: {Colors.TEXT};
}}

QLabel#h2 {{
    font-size: 16px;
    font-weight: 600;
    color: {Colors.TEXT};
}}

QLabel#subtitle {{
    font-size: 13px;
    color: {Colors.TEXT_SECONDARY};
}}

QLabel#muted {{
    font-size: 12px;
    color: {Colors.TEXT_MUTED};
}}

QLabel#sectionLabel {{
    font-size: 11px;
    font-weight: 700;
    color: {Colors.TEXT_SECONDARY};
    letter-spacing: 1px;
}}

/* ---------- Cards ---------- */

QFrame#card {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 12px;
}}

QFrame#dropZone {{
    background-color: {Colors.SURFACE};
    border: 2px dashed {Colors.BORDER_STRONG};
    border-radius: 14px;
}}

QFrame#dropZone:hover {{
    border-color: {Colors.PRIMARY};
    background-color: #FAFBFF;
}}

/* ---------- Buttons ---------- */

QPushButton {{
    background-color: {Colors.SURFACE};
    color: {Colors.TEXT};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 500;
    min-height: 16px;
}}

QPushButton:hover {{
    background-color: {Colors.SURFACE_ALT};
    border-color: {Colors.BORDER_STRONG};
}}

QPushButton:pressed {{
    background-color: #EEF0F4;
}}

QPushButton:disabled {{
    color: {Colors.TEXT_MUTED};
    background-color: {Colors.SURFACE_ALT};
    border-color: {Colors.BORDER};
}}

QPushButton#primary {{
    background-color: {Colors.PRIMARY};
    color: white;
    border: 1px solid {Colors.PRIMARY};
    font-weight: 600;
    padding: 10px 18px;
}}

QPushButton#primary:hover {{
    background-color: {Colors.PRIMARY_HOVER};
    border-color: {Colors.PRIMARY_HOVER};
}}

QPushButton#primary:pressed {{
    background-color: {Colors.PRIMARY_ACTIVE};
    border-color: {Colors.PRIMARY_ACTIVE};
}}

QPushButton#primary:disabled {{
    background-color: #CBD5E1;
    border-color: #CBD5E1;
    color: white;
}}

QPushButton#success {{
    background-color: {Colors.SUCCESS};
    color: white;
    border: 1px solid {Colors.SUCCESS};
    font-weight: 600;
    padding: 10px 18px;
}}

QPushButton#success:hover {{
    background-color: {Colors.SUCCESS_HOVER};
    border-color: {Colors.SUCCESS_HOVER};
}}

QPushButton#danger {{
    background-color: {Colors.SURFACE};
    color: {Colors.DANGER};
    border: 1px solid #FECACA;
}}

QPushButton#danger:hover {{
    background-color: {Colors.DANGER_SOFT};
    border-color: #FCA5A5;
}}

/* ---------- Inputs ---------- */

QLineEdit, QComboBox {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: {Colors.PRIMARY_SOFT};
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
    border-radius: 10px;
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

QTableWidget, QTableView {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 12px;
    gridline-color: transparent;
    font-size: 13px;
    selection-background-color: {Colors.PRIMARY_SOFT};
    selection-color: {Colors.TEXT};
    alternate-background-color: {Colors.SURFACE_ALT};
}}

QTableWidget::item, QTableView::item {{
    padding: 10px 8px;
    border: none;
    border-bottom: 1px solid {Colors.SURFACE_ALT};
}}

QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {Colors.PRIMARY_SOFT};
    color: {Colors.TEXT};
}}

QHeaderView::section {{
    background-color: {Colors.SURFACE_ALT};
    color: {Colors.TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
    padding: 10px 8px;
    font-size: 11px;
    font-weight: 700;
}}

QHeaderView::section:first {{
    border-top-left-radius: 12px;
}}

QHeaderView::section:last {{
    border-top-right-radius: 12px;
}}

QTableCornerButton::section {{
    background-color: {Colors.SURFACE_ALT};
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
}}

/* ---------- Badges (status pills) ---------- */

QLabel#badgeInfo {{
    background-color: {Colors.PRIMARY_SOFT};
    color: {Colors.PRIMARY_TEXT};
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#badgeWarning {{
    background-color: {Colors.WARNING_SOFT};
    color: {Colors.WARNING_TEXT};
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#badgeSuccess {{
    background-color: {Colors.SUCCESS_SOFT};
    color: {Colors.SUCCESS_TEXT};
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#badgeNeutral {{
    background-color: {Colors.SURFACE_ALT};
    color: {Colors.TEXT_SECONDARY};
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}

QLabel#badgeDanger {{
    background-color: {Colors.DANGER_SOFT};
    color: {Colors.DANGER_TEXT};
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}

/* ---------- Stat tiles ---------- */

QFrame#statTile {{
    background-color: {Colors.SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: 12px;
    padding: 4px;
}}

QLabel#statValue {{
    font-size: 28px;
    font-weight: 700;
    color: {Colors.TEXT};
}}

QLabel#statLabel {{
    font-size: 11px;
    color: {Colors.TEXT_SECONDARY};
    font-weight: 600;
    letter-spacing: 0.8px;
}}

/* ---------- Scrollbars ---------- */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: #CBD5E1;
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: #94A3B8;
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
    background: #CBD5E1;
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #94A3B8;
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
    font-size: 13px;
}}

QMessageBox {{
    background-color: {Colors.SURFACE};
}}

QMessageBox QLabel {{
    font-size: 13px;
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


def make_stat_tile(parent_layout, label_text: str, initial_value: str = "0"):
    """Возвращает (frame, value_label) — карточка с числом и подписью."""
    from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel
    from PySide6.QtCore import Qt

    frame = QFrame()
    frame.setObjectName("statTile")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(2)

    value = QLabel(initial_value)
    value.setObjectName("statValue")
    lay.addWidget(value)

    label = QLabel(label_text.upper())
    label.setObjectName("statLabel")
    lay.addWidget(label)

    parent_layout.addWidget(frame)
    return frame, value


def make_badge(text: str, kind: str = "neutral"):
    """kind: neutral|info|success|warning|danger"""
    from PySide6.QtWidgets import QLabel
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
    from PySide6.QtWidgets import QFrame
    line = QFrame()
    line.setObjectName("hLine")
    return line
