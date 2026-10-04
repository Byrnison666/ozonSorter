import threading
from datetime import datetime

from PySide2.QtCore import Qt, QThread, QUrl, Signal
from PySide2.QtGui import QDesktopServices, QGuiApplication
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton, QLabel,
    QLineEdit, QMessageBox, QFrame, QDialog,
)

from src.sync_config import adopt_legacy_state, forget_legacy, load_config, save_config
from src.yadisk_api import YaDiskError
from src.yandex_oauth import OAuthError, fetch_user, poll_token, request_device_code

from .sync_flow import ask_yes_no, show_message

APP_FOLDER = "Приложения/OzonSorter"

# Потоки входа, окно которых уже закрыто: ждут ответа сети, результат не нужен.
_abandoned_logins = set()


def describe_login_error(error: Exception) -> str:
    if isinstance(error, OAuthError):
        if error.code == "expired_token":
            return "Код устарел. Нажмите «Войти в Яндекс» ещё раз."
        if error.code == "access_denied":
            return "Вход отклонён на странице Яндекса."
        if error.code == "not_configured":
            return "В этой сборке программы не настроен вход в Яндекс."
        return f"Яндекс не выполнил вход ({error.code or error}). Повторите позже."
    if isinstance(error, YaDiskError):
        if error.is_tls_error:
            return ("Не удалось проверить защищённое соединение с Яндексом. "
                    "Проверьте дату и время на компьютере.")
        if error.is_network_error:
            return "Нет связи с Яндексом. Проверьте интернет (и отключите VPN) и повторите."
        return f"Яндекс ответил ошибкой {error.status}. Повторите позже."
    return f"Не удалось войти в Яндекс:\n{error}"


def stop_login_threads() -> None:
    """Перед выходом из программы: потоки входа ждут только ответа сети."""
    for worker in list(_abandoned_logins):
        if not worker.wait(1500):
            worker.terminate()
            worker.wait()
        _abandoned_logins.discard(worker)


class _LoginWorker(QThread):
    """Получить код, дождаться его подтверждения, узнать аккаунт."""
    code_ready = Signal(object)    # DeviceCode
    succeeded = Signal(object)     # (Tokens, uid, login)
    failed = Signal(object)        # исключение

    def __init__(self, device_name: str):
        super().__init__()
        self._device_name = device_name
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            code = request_device_code(self._device_name)
            self.code_ready.emit(code)
            while not self._stop.wait(code.interval):
                tokens = poll_token(code)
                if tokens is not None:
                    uid, login = fetch_user(tokens.access_token)
                    if not self._stop.is_set():
                        self.succeeded.emit((tokens, uid, login))
                    return
        except Exception as e:  # передаём в окно, там покажут
            if not self._stop.is_set():
                self.failed.emit(e)

    def abandon(self):
        _abandoned_logins.add(self)
        self.finished.connect(self._forget)
        if self.isFinished():
            self._forget()

    def _forget(self):
        _abandoned_logins.discard(self)
        self.deleteLater()


class LoginDialog(QDialog):
    """Вход по коду: пользователь вводит код на ya.ru/device в своём браузере."""

    def __init__(self, parent, device_name: str):
        super().__init__(parent)
        self.result_tokens = None
        self.error = None
        self._url = ""
        self.setWindowTitle("Вход в Яндекс")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self._text = QLabel("Получение кода…")
        self._text.setWordWrap(True)
        layout.addWidget(self._text)
        self._code = QLabel("")
        self._code.setAlignment(Qt.AlignCenter)
        self._code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._code.setStyleSheet("font-size: 28px; font-weight: 600; letter-spacing: 4px;")
        self._code.setVisible(False)
        layout.addWidget(self._code)
        row = QHBoxLayout()
        self._open_btn = QPushButton("Открыть страницу")
        self._open_btn.setObjectName("primary")
        self._open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._url)))
        self._open_btn.setVisible(False)
        row.addWidget(self._open_btn)
        self._copy_btn = QPushButton("Скопировать код")
        self._copy_btn.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self._code.text()))
        self._copy_btn.setVisible(False)
        row.addWidget(self._copy_btn)
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)

        self._worker = _LoginWorker(device_name)
        self._worker.code_ready.connect(self._on_code)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.start()

    def _on_code(self, code):
        self._url = code.verification_url
        self._text.setText(
            f"Откройте страницу {code.verification_url.replace('https://', '')} в браузере, "
            "где вы вошли в Яндекс, и введите код:")
        self._code.setText(code.user_code)
        for widget in (self._code, self._open_btn, self._copy_btn):
            widget.setVisible(True)
        self.adjustSize()

    def _on_success(self, result):
        self.result_tokens = result
        self._release_worker()
        self.accept()

    def _on_failure(self, error):
        self.error = error
        self._release_worker()
        self.reject()

    def _release_worker(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()
            worker.abandon()

    def reject(self):
        self._release_worker()
        super().reject()


class SettingsScreen(QWidget):
    """Настройки синхронизации базы с Яндекс.Диском и её ручной запуск."""

    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        h1 = QLabel("Настройки")
        h1.setObjectName("h1")
        title_block.addWidget(h1)
        sub = QLabel(
            "Синхронизация базы с Яндекс.Диском: одна и та же база на компьютере "
            "и на телефоне. Работайте по очереди — перед работой синхронизируйте, "
            "после работы тоже."
        )
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        title_block.addWidget(sub)
        layout.addLayout(title_block)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(20, 18, 20, 18)
        card_lay.setSpacing(14)

        card_title = QLabel("Яндекс.Диск")
        card_title.setObjectName("h2")
        card_lay.addWidget(card_title)

        self.account_label = QLabel("")
        self.account_label.setWordWrap(True)
        card_lay.addWidget(self.account_label)

        account_row = QHBoxLayout()
        account_row.setSpacing(10)
        self.login_btn = QPushButton("Войти в Яндекс")
        self.login_btn.clicked.connect(self.on_login)
        account_row.addWidget(self.login_btn)
        self.logout_btn = QPushButton("Выйти")
        self.logout_btn.clicked.connect(self.on_logout)
        account_row.addWidget(self.logout_btn)
        account_row.addStretch(1)
        card_lay.addLayout(account_row)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.device_edit = QLineEdit()
        self.device_edit.setPlaceholderText("как это устройство подписывать, например «ПК на складе»")
        form.addRow("Имя устройства:", self.device_edit)
        card_lay.addLayout(form)

        hint = QLabel(
            "Вход по коду: программа покажет код, его нужно ввести на странице "
            "ya.ru/device в браузере, где вы вошли в Яндекс. Пароль от Яндекса "
            f"программа не видит; доступ у неё есть только к папке «{APP_FOLDER}» на Диске."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        card_lay.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.clicked.connect(self.on_save)
        buttons.addWidget(self.save_btn)
        self.sync_btn = QPushButton("Синхронизировать")
        self.sync_btn.setObjectName("primary")
        self.sync_btn.clicked.connect(self.on_sync)
        buttons.addWidget(self.sync_btn)
        buttons.addStretch(1)
        card_lay.addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setObjectName("subtitle")
        self.status_label.setWordWrap(True)
        card_lay.addWidget(self.status_label)

        layout.addWidget(card)
        layout.addStretch()

        self._load()

    def _load(self):
        config = load_config()
        self.device_edit.setText(config.device_name)
        self._show_account(config)

    def _show_account(self, config):
        if config.is_configured:
            self.account_label.setText(
                f"Вход выполнен: {config.login or 'аккаунт Яндекса'}. "
                f"База хранится на Диске в папке «{APP_FOLDER}».")
        else:
            self.account_label.setText("Вход в Яндекс не выполнен — синхронизация выключена.")
        self.login_btn.setVisible(not config.is_configured)
        self.logout_btn.setVisible(config.is_configured)
        self.sync_btn.setEnabled(config.is_configured)

    def _collect(self):
        """Сохранённые настройки с именем устройства из формы."""
        config = load_config()
        config.device_name = self.device_edit.text().strip() or config.device_name
        return config

    def _save(self, config) -> bool:
        try:
            save_config(config)
        except OSError as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки:\n{e}")
            return False
        self._show_account(config)
        # Панель и кнопка синхронизации в левом меню зависят от входа.
        self.main_window.refresh_sync_indicator()
        return True

    def on_save(self):
        if self._save(self._collect()):
            self.status_label.setText("Настройки сохранены.")

    def on_login(self):
        config = self._collect()
        dialog = LoginDialog(self, config.device_name)
        dialog.exec_()
        result, error = dialog.result_tokens, dialog.error
        dialog.deleteLater()
        if result is None:
            if error is not None:
                show_message(self, QMessageBox.Warning, "Вход в Яндекс",
                             describe_login_error(error))
            return
        tokens, uid, login = result
        config = self._collect()
        config.token, config.refresh_token = tokens.access_token, tokens.refresh_token
        config.token_expires_at, config.uid, config.login = tokens.expires_at, uid, login
        config.token_unreadable = False
        adopt_legacy_state(config)
        forget_legacy(config)
        if self._save(config):
            self.status_label.setText(f"Вход выполнен в {datetime.now().strftime('%H:%M')}.")

    def on_logout(self):
        if not ask_yes_no(self, "Выход из Яндекса",
                          "Выйти из Яндекса? Синхронизация остановится до нового входа.",
                          default_yes=False):
            return
        config = self._collect()
        config.token = config.refresh_token = config.uid = config.login = ""
        config.token_expires_at = 0
        if self._save(config):
            self.status_label.setText("Выход выполнен.")

    def on_sync(self):
        config = self._collect()
        if not config.is_configured:
            QMessageBox.information(self, "Нужен вход", "Сначала войдите в Яндекс.")
            return
        if not self._save(config):
            return
        # Все диалоги (нет интернета, конфликт, итог) показывает главное окно.
        if self.main_window.sync_now(announce=True):
            self.status_label.setText(
                f"Синхронизировано в {datetime.now().strftime('%H:%M')}."
            )
        else:
            self.status_label.setText("Синхронизация не выполнена.")
