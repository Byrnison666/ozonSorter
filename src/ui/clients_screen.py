from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox,
    QMessageBox, QFrame, QAbstractItemView
)
from sqlalchemy import select

from src.models import Client, DeliveryPointPolicy, DeliveryPoint
from .theme import make_badge


class ClientsScreen(QWidget):
    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(8)

        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        h1 = QLabel("Клиенты")
        h1.setObjectName("h1")
        title_block.addWidget(h1)
        sub = QLabel("Постоянные Ozon ID наших клиентов и их точки выдачи.")
        sub.setObjectName("subtitle")
        title_block.addWidget(sub)
        header.addLayout(title_block, 1)

        self.add_btn = QPushButton("+ Добавить клиента")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self.on_add_client)
        header.addWidget(self.add_btn, 0, Qt.AlignTop)

        layout.addLayout(header)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(2, 2, 2, 2)
        card_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["Ozon ID", "ФИО", "Телефон", "Политика", "Точка по умолчанию"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(40)
        card_lay.addWidget(self.table)

        layout.addWidget(card, 1)

        self.refresh_table()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_table()

    def refresh_table(self):
        session = self.db_manager.get_session()
        try:
            clients = session.execute(
                select(Client).where(Client.is_active == True)
                .order_by(Client.ozon_client_id)
            ).scalars().all()

            self.table.setRowCount(0)
            for client in clients:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(client.ozon_client_id))
                self.table.setItem(row, 1, QTableWidgetItem(client.full_name or ""))
                self.table.setItem(row, 2, QTableWidgetItem(client.phone or ""))

                policy = client.delivery_point_policy.value
                kind = "info" if policy == "FIXED" else "warning"
                badge_text = "FIXED" if policy == "FIXED" else "MANUAL"
                self.table.setCellWidget(row, 3, make_badge(badge_text, kind))

                point_text = self._point_text(client.fixed_delivery_point)
                self.table.setItem(row, 4, QTableWidgetItem(point_text))
        finally:
            session.close()

    @staticmethod
    def _point_text(point):
        if point == DeliveryPoint.KOMSOMOLSKAYA_4:
            return "Комсомольская 4"
        if point == DeliveryPoint.KOLTSEVAYA_16:
            return "Кольцевая 16"
        return "—"

    def on_add_client(self):
        dialog = ClientDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        data = dialog.get_data()

        if not data['ozon_client_id'].isdigit():
            QMessageBox.warning(
                self, "Ошибка",
                "Ozon ID должен содержать только цифры (без дефисов и букв)."
            )
            return

        session = self.db_manager.get_session()
        try:
            existing = session.execute(
                select(Client).where(
                    Client.ozon_client_id == data['ozon_client_id']
                )
            ).scalar_one_or_none()
            if existing:
                QMessageBox.warning(
                    self, "Дубликат",
                    f"Клиент с ID {data['ozon_client_id']} уже существует."
                )
                return

            client = Client(
                ozon_client_id=data['ozon_client_id'],
                full_name=data['full_name'] or None,
                phone=data['phone'] or None,
                delivery_point_policy=DeliveryPointPolicy(data['policy']),
                fixed_delivery_point=(
                    DeliveryPoint(data['point']) if data['point'] else None
                ),
            )
            session.add(client)
            session.commit()
            self.refresh_table()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка",
                                 f"Не удалось добавить клиента:\n{e}")
        finally:
            session.close()


class ClientDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый клиент")
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        title = QLabel("Новый клиент")
        title.setObjectName("h2")
        outer.addWidget(title)

        sub = QLabel("Постоянный Ozon ID и точка выдачи.")
        sub.setObjectName("subtitle")
        outer.addWidget(sub)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("например, 0224933356")
        form.addRow("Ozon ID:", self.id_edit)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("необязательно")
        form.addRow("ФИО:", self.name_edit)

        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("необязательно")
        form.addRow("Телефон:", self.phone_edit)

        self.policy_combo = QComboBox()
        self.policy_combo.addItem("FIXED — все посылки на одну точку", "FIXED")
        self.policy_combo.addItem("MANUAL — распределяем вручную", "MANUAL")
        self.policy_combo.currentIndexChanged.connect(self.on_policy_changed)
        form.addRow("Политика:", self.policy_combo)

        self.point_combo = QComboBox()
        self.point_combo.addItem("Комсомольская 4", "KOMSOMOLSKAYA_4")
        self.point_combo.addItem("Кольцевая 16", "KOLTSEVAYA_16")
        form.addRow("Точка по умолчанию:", self.point_combo)

        outer.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Ok).setText("Сохранить")
        self.buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

        self.on_policy_changed()

    def on_policy_changed(self, *_):
        is_fixed = self.policy_combo.currentData() == "FIXED"
        self.point_combo.setEnabled(is_fixed)

    def get_data(self):
        return {
            'ozon_client_id': self.id_edit.text().strip(),
            'full_name': self.name_edit.text().strip(),
            'phone': self.phone_edit.text().strip(),
            'policy': self.policy_combo.currentData(),
            'point': (self.point_combo.currentData()
                      if self.policy_combo.currentData() == "FIXED"
                      else None),
        }
