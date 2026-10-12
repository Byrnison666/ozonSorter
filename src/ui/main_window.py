import os

from PySide2.QtCore import QRectF, Qt, QTimer
from PySide2.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide2.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QStackedWidget, QLabel, QFrame, QSizePolicy, QMessageBox,
    QStyle, QStyleOptionButton,
)

from src.stale_service import ALERT_DAYS
from src.sync_config import load_config
from src.sync_service import SyncStatus
from src.yadisk_api import YaDiskError

from .dashboard_screen import DashboardScreen
from .clients_screen import ClientsScreen
from .duplicates_screen import DuplicatesScreen
from .import_log_screen import ImportLogScreen
from .settings_screen import SettingsScreen, stop_login_threads
from .stale_screen import StaleScreen
from .theme import ASSETS_DIR, Colors, icon_label, icon_pixmap, set_button_icon
from .sync_flow import (
    BackgroundCheck, ask_yes_no, describe_error, has_local_changes, message_box,
    show_message, stop_pending_work, sync_interactive,
)


NAV_STRUCTURE = [
    ("ОСНОВНОЕ", [
        ("Главная", "dashboard", "house"),
    ]),
    ("РАБОТА", [
        ("Клиенты", "clients", "users"),
        ("Залежавшиеся посылки", "stale", "clock"),
        ("Дубли", "duplicates", "copy"),
    ]),
    ("СИСТЕМА", [
        ("Журнал импортов", "import_log", "history"),
        ("Настройки", "settings", "settings"),
    ]),
]

# Иконка и её цвет в карточке синхронизации: dirty False / True / None (не проверено).
_SYNC_ICONS = {
    False: ("cloud-check", Colors.SUCCESS),
    True: ("cloud-upload", Colors.WARNING),
    None: ("cloud-alert", Colors.WARNING),
}

# Экраны, работающие с базой: их пересоздаём, когда базу заменила версия с Диска.
_DB_SCREENS = {
    "dashboard": DashboardScreen,
    "clients": ClientsScreen,
    "stale": StaleScreen,
    "duplicates": DuplicatesScreen,
    "import_log": ImportLogScreen,
}


class NavButton(QPushButton):
    """Пункт меню (NavItem в макете): иконка, подпись, у активного — синяя полоса.
    Фон и высоту задаёт QSS, иконку и подпись — paintEvent: у QPushButton между
    ними фиксированные 4px, а иконка должна светлеть при наведении."""

    _ICON = 18
    _LEFT = 14
    _GAP = 14

    def __init__(self, title: str, icon_name: str):
        super().__init__(title)
        self.setObjectName("navButton")
        self.setCheckable(True)
        self._icon_dim = icon_pixmap(icon_name, Colors.INK_TEXT, self._ICON)
        self._icon_lit = icon_pixmap(icon_name, Colors.INK_TEXT_STRONG, self._ICON)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        self.style().drawControl(QStyle.CE_PushButtonBevel, option, painter, self)

        lit = self.isChecked() or self.underMouse()
        if self.isChecked():
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(Colors.PRIMARY))
            painter.drawRoundedRect(QRectF(0, 10, 3, self.height() - 20), 1.5, 1.5)

        top = (self.height() - self._ICON) // 2
        painter.drawPixmap(self._LEFT, top, self._icon_lit if lit else self._icon_dim)

        font = QFont(self.font())
        if self.isChecked():
            font.setFamilies(["Onest SemiBold", "Segoe UI"])
            font.setFamily("Onest SemiBold")
        painter.setFont(font)
        painter.setPen(QColor(Colors.INK_TEXT_STRONG if lit else Colors.INK_TEXT))
        text_rect = self.rect().adjusted(self._LEFT + self._ICON + self._GAP, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self, db_manager):
        super().__init__()
        self.db_manager = db_manager
        self.setWindowTitle("OzonSorter — Казакова 68")
        self.resize(1280, 820)
        self.setMinimumSize(1100, 700)

        icon_path = os.path.join(ASSETS_DIR, "icon.png")
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
        content_layout.setContentsMargins(40, 32, 40, 32)
        content_layout.setSpacing(0)

        self.stacked_widget = QStackedWidget()
        content_layout.addWidget(self.stacked_widget)

        self._screens = {
            key: screen(self.db_manager, self) for key, screen in _DB_SCREENS.items()
        }
        self._screens["settings"] = SettingsScreen(self.db_manager, self)
        self._current_key = "dashboard"
        self._sync_busy = False
        self._startup_check = None
        self._closing = False

        self._key_to_index = {}
        for key, widget in self._screens.items():
            self._key_to_index[key] = self.stacked_widget.addWidget(widget)

        root_layout.addWidget(self.content_area, 1)

        self.go_to("dashboard")
        self.refresh_sync_indicator()

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(256)
        sidebar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 24, 12, 16)
        layout.setSpacing(2)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 0, 0, 18)
        brand_row.setSpacing(12)
        mark = QLabel()
        mark.setFixedSize(40, 40)
        ratio = self.devicePixelRatioF()
        mark_pixmap = QPixmap(os.path.join(ASSETS_DIR, "icon.png")).scaled(
            round(40 * ratio), round(40 * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        mark_pixmap.setDevicePixelRatio(ratio)
        mark.setPixmap(mark_pixmap)
        brand_row.addWidget(mark, 0, Qt.AlignTop)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(2)
        brand = QLabel("OzonSorter")
        brand.setObjectName("brand")
        brand_text.addWidget(brand)
        sub = QLabel("Казакова 68 → Комсомольская и Кольцевая")
        sub.setObjectName("brandSub")
        sub.setWordWrap(True)
        brand_text.addWidget(sub)
        brand_row.addLayout(brand_text, 1)
        layout.addLayout(brand_row)

        self.nav_buttons = {}
        for group_title, items in NAV_STRUCTURE:
            group_label = QLabel(group_title)
            group_label.setObjectName("navGroup")
            font = group_label.font()
            font.setLetterSpacing(QFont.PercentageSpacing, 108)
            group_label.setFont(font)
            layout.addWidget(group_label)

            for title, key, icon_name in items:
                btn = NavButton(title, icon_name)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _checked=False, k=key: self.go_to(k))
                layout.addWidget(btn)
                self.nav_buttons[key] = btn

        layout.addStretch()

        self.sync_card = QFrame()
        self.sync_card.setObjectName("syncCard")
        card_layout = QVBoxLayout(self.sync_card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(12)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.sync_icon = icon_label("cloud-check", Colors.SUCCESS, 18, 32, "syncIcon")
        status_row.addWidget(self.sync_icon, 0, Qt.AlignTop)
        self.sync_label = QLabel("")
        self.sync_label.setObjectName("syncStatus")
        self.sync_label.setWordWrap(True)
        status_row.addWidget(self.sync_label, 1)
        card_layout.addLayout(status_row)
        self.sync_btn = QPushButton("Синхронизировать")
        self.sync_btn.setObjectName("syncButton")
        self.sync_btn.setCursor(Qt.PointingHandCursor)
        set_button_icon(self.sync_btn, "refresh-cw", Colors.INK_TEXT_STRONG, 16)
        self.sync_btn.clicked.connect(lambda _checked=False: self.sync_now(announce=True))
        card_layout.addWidget(self.sync_btn)
        layout.addWidget(self.sync_card)

        footer = QLabel("v1.11.0")
        footer.setObjectName("sidebarFooter")
        layout.addWidget(footer)

        return sidebar

    def show_return_alert(self):
        """«Залежавшиеся» с фильтром посылок, которые Ozon скоро может вернуть."""
        self._screens["stale"].set_min_days(ALERT_DAYS)
        self.go_to("stale")

    def go_to(self, key: str):
        if key not in self._key_to_index:
            return
        self._current_key = key
        self.stacked_widget.setCurrentIndex(self._key_to_index[key])
        for btn_key, btn in self.nav_buttons.items():
            btn.setChecked(btn_key == key)

    # --- синхронизация с Яндекс.Диском ---

    def release_db(self):
        """Закрыть соединения с базой: её файл сейчас будут читать или заменять."""
        for screen in self._screens.values():
            release = getattr(screen, "release", None)
            if release is not None:
                release()
        self.db_manager.engine.dispose()

    def reload_db(self):
        """Базу заменила версия с Диска: применить миграции и пересоздать экраны."""
        self.db_manager.create_tables()
        for key, screen in _DB_SCREENS.items():
            old = self._screens[key]
            index = self._key_to_index[key]
            new = screen(self.db_manager, self)
            self.stacked_widget.removeWidget(old)
            old.deleteLater()
            self.stacked_widget.insertWidget(index, new)
            self._screens[key] = new
        self.go_to(self._current_key)

    def refresh_sync_indicator(self):
        """Пометка в боковой панели: есть ли изменения, не выложенные на Диск."""
        config = load_config()
        self.sync_card.setVisible(config.is_configured)
        self.sync_label.setVisible(config.is_configured)
        self.sync_btn.setVisible(config.is_configured)
        if config.is_configured:
            dirty = has_local_changes(self, config)
            if dirty is None:
                text = "Яндекс.Диск: не удалось проверить изменения"
            elif dirty:
                text = "Яндекс.Диск: есть невыложенные изменения"
            else:
                text = "Яндекс.Диск: невыложенных изменений нет"
            self.sync_label.setText(text)
            icon_name, color = _SYNC_ICONS[dirty]
            self.sync_icon.setPixmap(icon_pixmap(icon_name, color, 18))
            # Невыложенные (и непроверенные) изменения должны бросаться в глаза.
            self.sync_label.setObjectName("syncStatus" if dirty is False else "syncStatusDirty")
            self.sync_label.style().unpolish(self.sync_label)
            self.sync_label.style().polish(self.sync_label)

    def sync_now(self, announce: bool = False, confirm_pull: bool = False,
                 assume_offline: bool = False) -> bool:
        """Синхронизировать со всеми диалогами. True — база совпадает с Диском."""
        config = load_config()
        if not config.is_configured or self._sync_busy:
            return False
        self._sync_busy = True
        try:
            return sync_interactive(self, config, announce=announce,
                                    confirm_pull=confirm_pull, assume_offline=assume_offline)
        finally:
            self._sync_busy = False
            self.refresh_sync_indicator()

    def on_data_changed(self):
        """Экран изменил базу: сразу попытаться выложить изменения на Диск."""
        self.sync_now(confirm_pull=True)

    def startup_sync(self):
        """Проверка Диска при запуске — в фоне, окно не блокируется."""
        config = load_config()
        if config.token_unreadable:
            show_message(
                self, QMessageBox.Warning, "Синхронизация выключена",
                "Сохранённый вход в Яндекс не удалось прочитать "
                "(сменилась учётная запись Windows или файл перенесён с другого "
                "компьютера).\n\nВойдите в Яндекс заново в «Настройках».",
            )
            return
        if not config.is_configured:
            return
        try:
            self._startup_check = BackgroundCheck(config, self.db_manager.db_path)
        except ValueError:
            return  # адрес API не https — синхронизация не работает
        self._startup_check.finished.connect(self._on_startup_check)
        self._startup_check.start()

    def _on_startup_check(self):
        worker, self._startup_check = self._startup_check, None
        if worker is None or self._closing:
            return
        error, check = worker.error, worker.check
        worker.deleteLater()
        self._handle_startup_result(error, check)

    def _handle_startup_result(self, error, check):
        if self._closing:
            return
        # Результат мог прийти, пока открыт чужой диалог (правка клиента, выбор
        # файла, вопрос при закрытии) или идёт другая синхронизация. Свои окна
        # поверх них не показываем, и база под открытым диалогом не меняется.
        if self._sync_busy or QApplication.activeModalWidget() is not None:
            QTimer.singleShot(500, lambda: self._handle_startup_result(error, check))
            return
        if error is not None:
            if isinstance(error, YaDiskError) and error.is_network_error:
                # Без интернета молчим, пока выкладывать нечего.
                if has_local_changes(self, load_config()) is True:
                    self.sync_now(confirm_pull=True, assume_offline=True)
                return
            show_message(self, QMessageBox.Warning, "Синхронизация", describe_error(error))
            return
        if check.status != SyncStatus.UP_TO_DATE:
            # Невыложенные с прошлого раза изменения: об успехе сообщаем.
            self.sync_now(confirm_pull=True,
                          announce=check.status == SyncStatus.LOCAL_AHEAD)

    def _stop_background_sync(self):
        if self._startup_check is not None:
            worker, self._startup_check = self._startup_check, None
            worker.finished.disconnect(self._on_startup_check)
            # Поток только читает — прервать его при выходе безопасно.
            if not worker.wait(1500):
                worker.terminate()
                worker.wait()
            worker.deleteLater()
        stop_pending_work()
        stop_login_threads()

    def _sync_before_close(self) -> bool:
        """Предложить выложить изменения. False — закрытие окна отменено."""
        config = load_config()
        if not config.is_configured:
            return True
        self.release_db()
        changed = has_local_changes(self, config)
        if changed is None:
            return ask_yes_no(
                self, "Не удалось проверить базу",
                "Не удалось проверить, все ли изменения выложены на Яндекс.Диск.\n"
                "Закрыть программу?",
                default_yes=False,
            )
        if not changed:
            return True
        box = message_box(
            self, QMessageBox.Question, "Изменения не выложены",
            "В базе есть изменения, которых нет на Яндекс.Диске.\nВыложить их сейчас?",
        )
        push = box.addButton("Выложить", QMessageBox.AcceptRole)
        skip = box.addButton("Закрыть без выгрузки", QMessageBox.DestructiveRole)
        cancel = box.addButton("Отмена", QMessageBox.RejectRole)
        box.setDefaultButton(push)
        box.setEscapeButton(cancel)
        box.exec_()
        if box.clickedButton() is skip:
            return True
        if box.clickedButton() is not push:
            return False
        if self.sync_now():
            return True
        return ask_yes_no(
            self, "Изменения не выложены",
            "Изменения так и не выложены на Яндекс.Диск.\nЗакрыть программу?",
            default_yes=False,
        )

    def closeEvent(self, event):
        if self._sync_busy:
            event.ignore()
            return
        try:
            allow = self._sync_before_close()
        except Exception as e:
            # Сбой проверки не должен запирать пользователя в программе.
            print(f"Sync check on close failed: {e}")
            allow = True
        if not allow:
            event.ignore()
            return
        self._closing = True
        self._stop_background_sync()
        super().closeEvent(event)
