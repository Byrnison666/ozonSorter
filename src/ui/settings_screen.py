from datetime import datetime

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton, QLabel,
    QLineEdit, QMessageBox, QFrame,
)

from src.sync_config import DEFAULT_REMOTE_DIR, load_config, save_config


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

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.login_edit = QLineEdit()
        self.login_edit.setPlaceholderText("логин Яндекса, например ivanov")
        form.addRow("Логин:", self.login_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("пароль приложения, не пароль от почты")
        form.addRow("Пароль приложения:", self.password_edit)

        self.remote_dir_edit = QLineEdit()
        self.remote_dir_edit.setPlaceholderText(DEFAULT_REMOTE_DIR)
        form.addRow("Папка на Диске:", self.remote_dir_edit)

        self.device_edit = QLineEdit()
        self.device_edit.setPlaceholderText("как это устройство подписывать, например «ПК на складе»")
        form.addRow("Имя устройства:", self.device_edit)
        card_lay.addLayout(form)

        hint = QLabel(
            "Пароль приложения создаётся в Яндекс ID: Безопасность → Пароли приложений → "
            "«Файлы WebDAV». Он хранится на этом компьютере в зашифрованном виде."
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
        self.login_edit.setText(config.login)
        self.password_edit.setText(config.password)
        self.remote_dir_edit.setText(config.remote_dir)
        self.device_edit.setText(config.device_name)

    def _collect(self):
        """Настройки из полей формы поверх сохранённых (адрес сервера в форме не правится)."""
        config = load_config()
        config.login = self.login_edit.text().strip()
        config.password = self.password_edit.text().strip()
        config.remote_dir = self.remote_dir_edit.text().strip().strip("/") or DEFAULT_REMOTE_DIR
        config.device_name = self.device_edit.text().strip() or config.device_name
        return config

    def on_save(self):
        try:
            save_config(self._collect())
        except OSError as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки:\n{e}")
            return
        self.status_label.setText("Настройки сохранены.")
        # Панель и кнопка синхронизации в левом меню зависят от учётных данных.
        self.main_window.refresh_sync_indicator()

    def on_sync(self):
        config = self._collect()
        if not config.is_configured:
            QMessageBox.information(
                self, "Нужны логин и пароль",
                "Укажите логин Яндекса и пароль приложения.",
            )
            return
        try:
            save_config(config)
        except OSError as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки:\n{e}")
            return
        # Все диалоги (нет интернета, конфликт, итог) показывает главное окно.
        if self.main_window.sync_now(announce=True):
            self.status_label.setText(
                f"Синхронизировано в {datetime.now().strftime('%H:%M')}."
            )
        else:
            self.status_label.setText("Синхронизация не выполнена.")
