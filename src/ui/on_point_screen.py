from datetime import datetime

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QLineEdit, QMessageBox, QAbstractItemView,
    QInputDialog, QFrame
)
from sqlalchemy import select, or_

from src.models import Shipment, Client, AssignmentStatus, DeliveryPoint
from .theme import Colors


class OnPointScreen(QWidget):
    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        header = QVBoxLayout()
        header.setSpacing(4)
        h1 = QLabel("Остатки на точках")
        h1.setObjectName("h1")
        header.addWidget(h1)
        sub = QLabel(
            "Посылки, привезённые на точку и ожидающие выдачи клиенту."
        )
        sub.setObjectName("subtitle")
        header.addWidget(sub)
        layout.addLayout(header)

        # Filters card
        filter_card = QFrame()
        filter_card.setObjectName("card")
        filter_lay = QHBoxLayout(filter_card)
        filter_lay.setContentsMargins(16, 12, 16, 12)
        filter_lay.setSpacing(10)

        point_label = QLabel("Точка")
        point_label.setObjectName("sectionLabel")
        filter_lay.addWidget(point_label)
        self.point_combo = QComboBox()
        self.point_combo.addItem("Обе точки", None)
        self.point_combo.addItem("Комсомольская 4", DeliveryPoint.KOMSOMOLSKAYA_4)
        self.point_combo.addItem("Кольцевая 16", DeliveryPoint.KOLTSEVAYA_16)
        self.point_combo.setMinimumWidth(180)
        self.point_combo.currentIndexChanged.connect(self.refresh)
        filter_lay.addWidget(self.point_combo)

        filter_lay.addSpacing(14)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "Поиск по Ozon ID, номеру отправления или ФИО…"
        )
        self.search_edit.textChanged.connect(self.refresh)
        filter_lay.addWidget(self.search_edit, 1)

        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.clicked.connect(self.refresh)
        filter_lay.addWidget(self.btn_refresh)

        layout.addWidget(filter_card)

        # Summary line
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("muted")
        layout.addWidget(self.summary_label)

        # Action card
        action_card = QFrame()
        action_card.setObjectName("card")
        action_lay = QHBoxLayout(action_card)
        action_lay.setContentsMargins(16, 10, 16, 10)
        action_lay.setSpacing(8)

        actions_label = QLabel("Действия с выделенными")
        actions_label.setObjectName("sectionLabel")
        action_lay.addWidget(actions_label)

        self.btn_deliver = QPushButton("Отметить выдачу")
        self.btn_deliver.setObjectName("success")
        self.btn_deliver.clicked.connect(self.on_deliver)
        action_lay.addWidget(self.btn_deliver)

        self.btn_move = QPushButton("Перенести на другую точку")
        self.btn_move.clicked.connect(self.on_move)
        action_lay.addWidget(self.btn_move)

        self.btn_delete = QPushButton("Удалить…")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.clicked.connect(self.on_delete)
        action_lay.addWidget(self.btn_delete)

        action_lay.addStretch()

        layout.addWidget(action_card)

        # Table card
        table_card = QFrame()
        table_card.setObjectName("card")
        table_card_lay = QVBoxLayout(table_card)
        table_card_lay.setContentsMargins(2, 2, 2, 2)
        table_card_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Номер отправления", "Ozon ID", "ФИО",
            "Товар", "Ячейка", "Точка",
            "Привезена", "Дней на точке",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(40)
        table_card_lay.addWidget(self.table)

        layout.addWidget(table_card, 1)

        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        session = self.db_manager.get_session()
        try:
            stmt = (
                select(Shipment, Client)
                .join(Client, Shipment.client_id == Client.id, isouter=True)
                .where(Shipment.assignment_status == AssignmentStatus.ON_POINT)
                .order_by(Shipment.shipped_to_point_at.desc().nullslast())
            )
            point = self.point_combo.currentData()
            if point is not None:
                stmt = stmt.where(Shipment.assigned_point == point)

            search = self.search_edit.text().strip()
            if search:
                pattern = f"%{search}%"
                stmt = stmt.where(or_(
                    Shipment.posting_number.like(pattern),
                    Shipment.ozon_client_id_raw.like(pattern),
                    Client.full_name.like(pattern),
                ))

            rows = session.execute(stmt).all()

            self.table.setRowCount(0)
            today = datetime.now()
            koms_count = 0
            kolt_count = 0
            damaged_bg = QColor(Colors.DAMAGED_BG)

            for shipment, client in rows:
                if shipment.assigned_point == DeliveryPoint.KOMSOMOLSKAYA_4:
                    koms_count += 1
                elif shipment.assigned_point == DeliveryPoint.KOLTSEVAYA_16:
                    kolt_count += 1

                r = self.table.rowCount()
                self.table.insertRow(r)
                self._set_cell(r, 0, shipment.posting_number, shipment.id)
                self._set_cell(r, 1, shipment.ozon_client_id_raw or "")
                self._set_cell(r, 2, (client.full_name if client else "") or "—")
                self._set_cell(
                    r, 3,
                    shipment.product_name or shipment.product_label or ""
                )
                self._set_cell(r, 4, shipment.cell or "")
                self._set_cell(r, 5, self._point_short(shipment.assigned_point))
                shipped_at = shipment.shipped_to_point_at
                self._set_cell(
                    r, 6,
                    shipped_at.strftime("%d.%m.%Y") if shipped_at else "—"
                )
                self._set_cell(
                    r, 7,
                    str((today - shipped_at).days) if shipped_at else "—"
                )

                if shipment.is_damaged:
                    for c in range(self.table.columnCount()):
                        item = self.table.item(r, c)
                        if item:
                            item.setBackground(damaged_bg)

            self.summary_label.setText(
                f"Показано: {self.table.rowCount()}    •    "
                f"Комсомольская 4: {koms_count}    •    "
                f"Кольцевая 16: {kolt_count}"
            )
        finally:
            session.close()

    def _set_cell(self, row, col, text, shipment_id=None):
        item = QTableWidgetItem(text)
        if shipment_id is not None:
            item.setData(Qt.UserRole, shipment_id)
        self.table.setItem(row, col, item)

    def _selected_ids(self):
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                sid = item.data(Qt.UserRole)
                if sid is not None:
                    ids.append(int(sid))
        return ids

    @staticmethod
    def _point_short(point):
        if point == DeliveryPoint.KOMSOMOLSKAYA_4:
            return "Комсомольская 4"
        if point == DeliveryPoint.KOLTSEVAYA_16:
            return "Кольцевая 16"
        return "—"

    def on_deliver(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Нет выбранных",
                                    "Выделите хотя бы одну строку.")
            return
        confirm = QMessageBox.question(
            self, "Подтверждение",
            f"Отметить {len(ids)} посылок как выданные клиенту?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        session = self.db_manager.get_session()
        try:
            shipments = session.execute(
                select(Shipment).where(Shipment.id.in_(ids))
            ).scalars().all()
            now = datetime.now()
            for sh in shipments:
                if sh.assignment_status != AssignmentStatus.ON_POINT:
                    continue
                sh.assignment_status = AssignmentStatus.DELIVERED
                sh.delivered_at = now
            session.commit()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
            return
        finally:
            session.close()
        self.refresh()

    def on_move(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Нет выбранных",
                                    "Выделите хотя бы одну строку.")
            return
        choices = ["Комсомольская 4", "Кольцевая 16"]
        choice, ok = QInputDialog.getItem(
            self, "Перенос на другую точку",
            "Выберите целевую точку:", choices, 0, False
        )
        if not ok:
            return
        target = (DeliveryPoint.KOMSOMOLSKAYA_4 if choice == "Комсомольская 4"
                  else DeliveryPoint.KOLTSEVAYA_16)

        session = self.db_manager.get_session()
        try:
            shipments = session.execute(
                select(Shipment).where(Shipment.id.in_(ids))
            ).scalars().all()
            for sh in shipments:
                sh.assigned_point = target
            session.commit()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось перенести:\n{e}")
            return
        finally:
            session.close()
        self.refresh()

    def on_delete(self):
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Нет выбранных",
                                    "Выделите хотя бы одну строку.")
            return
        reason, ok = QInputDialog.getText(
            self, "Удаление",
            "Укажите причину удаления (обязательно):"
        )
        if not ok or not reason.strip():
            QMessageBox.warning(self, "Отменено",
                                "Удаление без причины не разрешено.")
            return
        confirm = QMessageBox.question(
            self, "Подтверждение удаления",
            f"Удалить {len(ids)} посылок безвозвратно?\nПричина: {reason}",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        session = self.db_manager.get_session()
        try:
            shipments = session.execute(
                select(Shipment).where(Shipment.id.in_(ids))
            ).scalars().all()
            for sh in shipments:
                session.delete(sh)
            session.commit()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить:\n{e}")
            return
        finally:
            session.close()
        self.refresh()
