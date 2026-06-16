from datetime import datetime

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QMessageBox, QAbstractItemView, QFrame
)
from sqlalchemy import select, or_

from src.models import Shipment, Client, AssignmentStatus, DeliveryPoint
from .theme import Colors


class IssueScreen(QWidget):
    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window
        self.current_query = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        header = QVBoxLayout()
        header.setSpacing(4)
        h1 = QLabel("Выдача")
        h1.setObjectName("h1")
        header.addWidget(h1)
        sub = QLabel(
            "Введите Ozon ID клиента или номер отправления и нажмите Enter — "
            "найденные посылки сразу выделятся."
        )
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        header.addWidget(sub)
        layout.addLayout(header)

        # Search card — главная фишка экрана
        search_card = QFrame()
        search_card.setObjectName("card")
        search_lay = QVBoxLayout(search_card)
        search_lay.setContentsMargins(20, 18, 20, 18)
        search_lay.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchLarge")
        self.search_edit.setPlaceholderText(
            "Например: 0224933356 или 0224933356-0181-1"
        )
        self.search_edit.returnPressed.connect(self.on_search)
        search_row.addWidget(self.search_edit, 1)

        self.btn_search = QPushButton("Найти")
        self.btn_search.setObjectName("primary")
        self.btn_search.setMinimumHeight(44)
        self.btn_search.clicked.connect(self.on_search)
        search_row.addWidget(self.btn_search)

        self.btn_clear = QPushButton("Сброс")
        self.btn_clear.setMinimumHeight(44)
        self.btn_clear.clicked.connect(self.on_clear)
        search_row.addWidget(self.btn_clear)
        search_lay.addLayout(search_row)

        self.status_label = QLabel(
            "Готов к работе. Сканер штрихкода вводит данные сюда автоматически."
        )
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        search_lay.addWidget(self.status_label)

        layout.addWidget(search_card)

        # Found shipments
        found_label_row = QHBoxLayout()
        found_label = QLabel("Найденные посылки на точках")
        found_label.setObjectName("h2")
        found_label_row.addWidget(found_label)
        found_label_row.addStretch()

        self.btn_deliver_selected = QPushButton("Выдать выбранные")
        self.btn_deliver_selected.setObjectName("success")
        self.btn_deliver_selected.clicked.connect(self.on_deliver_selected)
        found_label_row.addWidget(self.btn_deliver_selected)

        self.btn_deliver_all = QPushButton("Выдать все найденные")
        self.btn_deliver_all.clicked.connect(self.on_deliver_all)
        found_label_row.addWidget(self.btn_deliver_all)

        layout.addLayout(found_label_row)

        table_card = QFrame()
        table_card.setObjectName("card")
        table_lay = QVBoxLayout(table_card)
        table_lay.setContentsMargins(2, 2, 2, 2)
        table_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Номер отправления", "Ozon ID", "ФИО",
            "Товар", "Ячейка", "Точка", "Дней на точке",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(40)
        table_lay.addWidget(self.table)

        layout.addWidget(table_card, 1)

        # Recent issues
        recent_label = QLabel("Последние выдачи")
        recent_label.setObjectName("h2")
        layout.addWidget(recent_label)

        recent_card = QFrame()
        recent_card.setObjectName("card")
        recent_lay = QVBoxLayout(recent_card)
        recent_lay.setContentsMargins(2, 2, 2, 2)
        recent_lay.setSpacing(0)

        self.recent_table = QTableWidget()
        self.recent_table.setColumnCount(5)
        self.recent_table.setHorizontalHeaderLabels(
            ["Время", "Номер отправления", "Ozon ID", "ФИО", "Точка"]
        )
        self.recent_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.recent_table.verticalHeader().setVisible(False)
        self.recent_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.recent_table.setShowGrid(False)
        self.recent_table.setFrameShape(QFrame.NoFrame)
        self.recent_table.verticalHeader().setDefaultSectionSize(36)
        self.recent_table.setMaximumHeight(220)
        recent_lay.addWidget(self.recent_table)

        layout.addWidget(recent_card)

        self.refresh_recent()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_recent()
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def on_search(self):
        query = self.search_edit.text().strip()
        if not query:
            self.table.setRowCount(0)
            self.status_label.setText("Введите Ozon ID или номер отправления.")
            return
        self.current_query = query
        self._load_results(query)

    def on_clear(self):
        self.search_edit.clear()
        self.current_query = ""
        self.table.setRowCount(0)
        self.status_label.setText(
            "Готов к работе. Сканер штрихкода вводит данные сюда автоматически."
        )
        self.search_edit.setFocus()

    def _load_results(self, query: str):
        session = self.db_manager.get_session()
        try:
            stmt = (
                select(Shipment, Client)
                .join(Client, Shipment.client_id == Client.id, isouter=True)
                .where(Shipment.assignment_status == AssignmentStatus.ON_POINT)
                .where(or_(
                    Shipment.posting_number == query,
                    Shipment.ozon_client_id_raw == query,
                ))
                .order_by(Shipment.assigned_point, Shipment.cell)
            )
            rows = session.execute(stmt).all()

            self.table.setRowCount(0)
            now = datetime.now()
            damaged_bg = QColor(Colors.DAMAGED_BG)

            for shipment, client in rows:
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
                days = "—"
                if shipment.shipped_to_point_at:
                    days = str((now - shipment.shipped_to_point_at).days)
                self._set_cell(r, 6, days)

                if shipment.is_damaged:
                    for c in range(self.table.columnCount()):
                        item = self.table.item(r, c)
                        if item:
                            item.setBackground(damaged_bg)

            self.table.selectAll()
            count = self.table.rowCount()
            if count == 0:
                self.status_label.setText(
                    f"По запросу «{query}» нет посылок на точках. "
                    f"Возможно, не привезена или уже выдана."
                )
            else:
                self.status_label.setText(
                    f"Найдено {count} посылок. Все выделены — "
                    f"нажмите «Выдать выбранные»."
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

    def _all_ids(self):
        ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
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

    def on_deliver_selected(self):
        self._deliver(self._selected_ids())

    def on_deliver_all(self):
        self._deliver(self._all_ids())

    def _deliver(self, ids):
        if not ids:
            QMessageBox.information(self, "Нет посылок",
                                    "Сначала найдите посылку и выделите её.")
            return
        session = self.db_manager.get_session()
        try:
            shipments = session.execute(
                select(Shipment).where(Shipment.id.in_(ids))
            ).scalars().all()
            now = datetime.now()
            count = 0
            for sh in shipments:
                if sh.assignment_status != AssignmentStatus.ON_POINT:
                    continue
                sh.assignment_status = AssignmentStatus.DELIVERED
                sh.delivered_at = now
                count += 1
            session.commit()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
            return
        finally:
            session.close()

        self.status_label.setText(f"Выдано {count} посылок.")
        if self.current_query:
            self._load_results(self.current_query)
        else:
            self.table.setRowCount(0)
        self.refresh_recent()
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def refresh_recent(self):
        session = self.db_manager.get_session()
        try:
            stmt = (
                select(Shipment, Client)
                .join(Client, Shipment.client_id == Client.id, isouter=True)
                .where(Shipment.assignment_status == AssignmentStatus.DELIVERED)
                .where(Shipment.delivered_at.is_not(None))
                .order_by(Shipment.delivered_at.desc())
                .limit(20)
            )
            rows = session.execute(stmt).all()
            self.recent_table.setRowCount(0)
            for shipment, client in rows:
                r = self.recent_table.rowCount()
                self.recent_table.insertRow(r)
                ts = (shipment.delivered_at.strftime("%d.%m.%Y %H:%M")
                      if shipment.delivered_at else "")
                self.recent_table.setItem(r, 0, QTableWidgetItem(ts))
                self.recent_table.setItem(
                    r, 1, QTableWidgetItem(shipment.posting_number)
                )
                self.recent_table.setItem(
                    r, 2, QTableWidgetItem(shipment.ozon_client_id_raw or "")
                )
                self.recent_table.setItem(
                    r, 3,
                    QTableWidgetItem((client.full_name if client else "") or "—")
                )
                self.recent_table.setItem(
                    r, 4,
                    QTableWidgetItem(self._point_short(shipment.assigned_point))
                )
        finally:
            session.close()
