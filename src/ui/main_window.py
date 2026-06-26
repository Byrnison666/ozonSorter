import os

from PySide2.QtGui import QIcon
from PySide2.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QStackedWidget, QLabel, QFrame, QSizePolicy
)

from .dashboard_screen import DashboardScreen
from .clients_screen import ClientsScreen
from .control_screen import ControlScreen
from .on_point_screen import OnPointScreen
from .issue_screen import IssueScreen


NAV_STRUCTURE = [
    ("ОСНОВНОЕ", [
        ("Главная", "dashboard"),
    ]),
    ("РАБОТА", [
        ("Клиенты", "clients"),
        ("Контроль посылок", "control"),
        ("Остатки на точках", "on_point"),
        ("Выдача", "issue"),
    ]),
    ("СИСТЕМА", [
        ("Журнал импортов", "import_log"),
        ("Настройки", "settings"),
    ]),
]


class MainWindow(QMainWindow):
    def __init__(self, db_manager):
        super().__init__()
        self.db_manager = db_manager
        self.setWindowTitle("OzonSorter — Казакова 68")
        self.resize(1280, 820)
        self.setMinimumSize(1100, 700)

        icon_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "assets", "icon.png"
        )
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        root = QWidget()
        root.setObjectName("contentRoot")
        self.setCentralWidget(root)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = self._build_sidebar()
        root_layout.addWidget(self.sidebar)

        self.content_area = QWidget()
        self.content_area.setObjectName("contentRoot")
        content_layout = QVBoxLayout(self.content_area)
        content_layout.setContentsMargins(36, 30, 36, 30)
        content_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()
        content_layout.addWidget(self.stacked_widget)

        self._screens = {
            "dashboard": DashboardScreen(self.db_manager, self),
            "clients": ClientsScreen(self.db_manager, self),
            "control": ControlScreen(self.db_manager, self),
            "on_point": OnPointScreen(self.db_manager, self),
            "issue": IssueScreen(self.db_manager, self),
            "import_log": self._placeholder_screen(
                "Журнал импортов",
                "История всех загрузок с детализацией по строкам.",
            ),
            "settings": self._placeholder_screen(
                "Настройки",
                "Папки сохранения, политика существующих файлов, бэкап БД.",
            ),
        }

        self._key_to_index = {}
        for key, widget in self._screens.items():
            self._key_to_index[key] = self.stacked_widget.addWidget(widget)

        root_layout.addWidget(self.content_area, 1)

        self.go_to("dashboard")

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(248)
        sidebar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QLabel("OzonSorter")
        brand.setObjectName("brand")
        layout.addWidget(brand)

        sub = QLabel("Казакова 68 → Комсомольская и Кольцевая")
        sub.setObjectName("brandSub")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self.nav_buttons = {}
        for group_title, items in NAV_STRUCTURE:
            group_label = QLabel(group_title)
            group_label.setObjectName("navGroup")
            layout.addWidget(group_label)

            for title, key in items:
                btn = QPushButton(title)
                btn.setObjectName("navButton")
                btn.setCheckable(True)
                btn.setCursor(self.cursor())
                btn.clicked.connect(lambda _checked=False, k=key: self.go_to(k))
                layout.addWidget(btn)
                self.nav_buttons[key] = btn

        layout.addStretch()

        footer = QLabel("v1.5.1  •  локальная БД")
        footer.setObjectName("sidebarFooter")
        layout.addWidget(footer)

        return sidebar

    def _placeholder_screen(self, title: str, subtitle: str) -> QWidget:
        screen = QWidget()
        screen.setObjectName("contentRoot")
        layout = QVBoxLayout(screen)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        h1 = QLabel(title)
        h1.setObjectName("h1")
        layout.addWidget(h1)

        sub = QLabel(subtitle)
        sub.setObjectName("subtitle")
        layout.addWidget(sub)

        spacer = QLabel("\nЭкран в разработке.")
        spacer.setObjectName("muted")
        layout.addWidget(spacer)

        layout.addStretch()
        return screen

    def go_to(self, key: str):
        if key not in self._key_to_index:
            return
        self.stacked_widget.setCurrentIndex(self._key_to_index[key])
        for btn_key, btn in self.nav_buttons.items():
            btn.setChecked(btn_key == key)
