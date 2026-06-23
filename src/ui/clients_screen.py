from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox,
    QMessageBox, QFrame, QAbstractItemView, QFileDialog
)
from sqlalchemy import select

from src.models import Client, DeliveryPoint
from src.client_import_service import ClientImportService


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

        self.template_btn = QPushButton("Шаблон")
        self.template_btn.clicked.connect(self.on_download_template)
        header.addWidget(self.template_btn, 0, Qt.AlignTop)

        self.import_btn = QPushButton("Импорт из файла")
        self.import_btn.clicked.connect(self.on_import_file)
        header.addWidget(self.import_btn, 0, Qt.AlignTop)

        self.edit_btn = QPushButton("Изменить")
        self.edit_btn.clicked.connect(self.on_edit_client)
        header.addWidget(self.edit_btn, 0, Qt.AlignTop)

        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.clicked.connect(self.on_delete_client)
        header.addWidget(self.delete_btn, 0, Qt.AlignTop)

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
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Ozon ID", "ФИО", "Телефон", "Точка"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.doubleClicked.connect(lambda _idx: self.on_edit_client())
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
                id_item = QTableWidgetItem(client.ozon_client_id)
                id_item.setData(Qt.UserRole, client.id)
                self.table.setItem(row, 0, id_item)
                self.table.setItem(row, 1, QTableWidgetItem(client.full_name or ""))
                self.table.setItem(row, 2, QTableWidgetItem(client.phone or ""))
                self.table.setItem(
                    row, 3, QTableWidgetItem(self._point_text(client.fixed_delivery_point))
                )
        finally:
            session.close()

    @staticmethod
    def _point_text(point):
        if point == DeliveryPoint.KOMSOMOLSKAYA_4:
            return "Комсомольская 4"
        if point == DeliveryPoint.KOLTSEVAYA_16:
            return "Кольцевая 16"
        return "—"

    def _selected_client_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def on_add_client(self):
        dialog = ClientDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        data = dialog.get_data()
        if not self._validate(data):
            return

        session = self.db_manager.get_session()
        try:
            existing = session.execute(
                select(Client).where(Client.ozon_client_id == data['ozon_client_id'])
            ).scalar_one_or_none()
            if existing:
                QMessageBox.warning(
                    self, "Дубликат",
                    f"Клиент с ID {data['ozon_client_id']} уже существует."
                )
                return

            session.add(Client(
                ozon_client_id=data['ozon_client_id'],
                full_name=data['full_name'] or None,
                phone=data['phone'] or None,
                fixed_delivery_point=DeliveryPoint(data['point']),
            ))
            session.commit()
            self.refresh_table()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось добавить клиента:\n{e}")
        finally:
            session.close()

    def on_edit_client(self):
        client_id = self._selected_client_id()
        if client_id is None:
            QMessageBox.information(self, "Нет выбора", "Выберите клиента в списке.")
            return

        session = self.db_manager.get_session()
        try:
            client = session.get(Client, client_id)
            if client is None:
                return
            initial = {
                'ozon_client_id': client.ozon_client_id,
                'full_name': client.full_name or "",
                'phone': client.phone or "",
                'point': client.fixed_delivery_point.value if client.fixed_delivery_point else None,
            }
            dialog = ClientDialog(self, initial=initial)
            if dialog.exec_() != QDialog.Accepted:
                return
            data = dialog.get_data()
            if not self._validate(data):
                return

            # Запрет на смену id в чужой существующий
            if data['ozon_client_id'] != client.ozon_client_id:
                clash = session.execute(
                    select(Client).where(
                        Client.ozon_client_id == data['ozon_client_id'],
                        Client.id != client.id,
                    )
                ).scalar_one_or_none()
                if clash:
                    QMessageBox.warning(
                        self, "Дубликат",
                        f"Клиент с ID {data['ozon_client_id']} уже существует."
                    )
                    return

            client.ozon_client_id = data['ozon_client_id']
            client.full_name = data['full_name'] or None
            client.phone = data['phone'] or None
            client.fixed_delivery_point = DeliveryPoint(data['point'])
            session.commit()
            self.refresh_table()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось изменить клиента:\n{e}")
        finally:
            session.close()

    def on_delete_client(self):
        client_id = self._selected_client_id()
        if client_id is None:
            QMessageBox.information(self, "Нет выбора", "Выберите клиента в списке.")
            return

        session = self.db_manager.get_session()
        try:
            client = session.get(Client, client_id)
            if client is None:
                return
            name = client.full_name or client.ozon_client_id
            if QMessageBox.question(
                self, "Удалить клиента",
                f"Удалить клиента «{name}» из списка?\n"
                f"История его посылок сохранится, новые перестанут к нему относиться.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            ) != QMessageBox.Yes:
                return
            client.is_active = False
            session.commit()
            self.refresh_table()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить клиента:\n{e}")
        finally:
            session.close()

    def _validate(self, data) -> bool:
        if not data['ozon_client_id'].isdigit():
            QMessageBox.warning(
                self, "Ошибка",
                "Ozon ID должен содержать только цифры (без дефисов и букв)."
            )
            return False
        if not data['point']:
            QMessageBox.warning(self, "Ошибка", "Выберите точку выдачи.")
            return False
        return True

    def on_download_template(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить шаблон", "Шаблон_клиентов.xlsx", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return
        try:
            ClientImportService.write_template(file_path)
            QMessageBox.information(
                self, "Шаблон сохранён",
                f"Заполните файл и загрузите через «Импорт из файла»:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить шаблон:\n{e}")

    def on_import_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл с клиентами", "", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        session = self.db_manager.get_session()
        try:
            result = ClientImportService(session).import_clients(file_path)
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка импорта",
                                 f"Не удалось импортировать файл:\n{e}")
            return
        finally:
            session.close()

        self.refresh_table()

        msg = (
            f"Обработано строк: {result.data_rows}\n"
            f"Добавлено: {result.added}\n"
            f"Обновлено: {result.updated}\n"
            f"Ошибок: {len(result.errors)}"
        )
        if result.errors:
            shown = result.errors[:10]
            lines = "\n".join(f"  строка {r}: {m}" for r, m in shown)
            more = f"\n  …ещё {len(result.errors) - len(shown)}" if len(result.errors) > len(shown) else ""
            msg += f"\n\nНе загружены:\n{lines}{more}"
            QMessageBox.warning(self, "Импорт завершён с ошибками", msg)
        else:
            QMessageBox.information(self, "Импорт завершён", msg)


class ClientDialog(QDialog):
    def __init__(self, parent=None, initial=None):
        super().__init__(parent)
        is_edit = initial is not None
        title_text = "Изменить клиента" if is_edit else "Новый клиент"
        self.setWindowTitle(title_text)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        title = QLabel(title_text)
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

        self.point_combo = QComboBox()
        self.point_combo.addItem("Комсомольская 4", "KOMSOMOLSKAYA_4")
        self.point_combo.addItem("Кольцевая 16", "KOLTSEVAYA_16")
        form.addRow("Точка выдачи:", self.point_combo)

        outer.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Сохранить")
        self.buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

        if is_edit:
            self.id_edit.setText(initial.get('ozon_client_id', ""))
            self.name_edit.setText(initial.get('full_name', ""))
            self.phone_edit.setText(initial.get('phone', ""))
            idx = self.point_combo.findData(initial.get('point'))
            if idx >= 0:
                self.point_combo.setCurrentIndex(idx)

    def get_data(self):
        return {
            'ozon_client_id': self.id_edit.text().strip(),
            'full_name': self.name_edit.text().strip(),
            'phone': self.phone_edit.text().strip(),
            'point': self.point_combo.currentData(),
        }
