from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QAbstractItemView, QMessageBox
)

from src.models import AssignmentStatus, DeliveryPoint
from src.shipment_control_service import ShipmentControlService
from .theme import make_badge


_STATUS_BADGE = {
    AssignmentStatus.TO_SHIP: ("К отгрузке", "info"),
    AssignmentStatus.ON_POINT: ("На точке", "warning"),
    AssignmentStatus.DELIVERED: ("Выдана", "success"),
}

_POINT_LABEL = {
    DeliveryPoint.KOMSOMOLSKAYA_4: "Комсомольская 4",
    DeliveryPoint.KOLTSEVAYA_16: "Кольцевая 16",
}


class ControlScreen(QWidget):
    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        # --- Header ---
        head = QVBoxLayout()
        head.setSpacing(4)
        h1 = QLabel("Контроль посылок")
        h1.setObjectName("h1")
        head.addWidget(h1)
        sub = QLabel("Все наши посылки: поиск, статус и сброс базы по точке.")
        sub.setObjectName("subtitle")
        head.addWidget(sub)
        layout.addLayout(head)

        # --- Filters + actions card ---
        bar = QFrame()
        bar.setObjectName("card")
        bar_lay = QVBoxLayout(bar)
        bar_lay.setContentsMargins(16, 14, 16, 14)
        bar_lay.setSpacing(12)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по номеру отправления или Ozon ID…")
        self.search.textChanged.connect(self.refresh_table)
        filters.addWidget(self.search, 1)

        self.point_combo = QComboBox()
        self.point_combo.addItem("Все точки", None)
        self.point_combo.addItem("Комсомольская 4", DeliveryPoint.KOMSOMOLSKAYA_4)
        self.point_combo.addItem("Кольцевая 16", DeliveryPoint.KOLTSEVAYA_16)
        self.point_combo.currentIndexChanged.connect(self.refresh_table)
        filters.addWidget(self.point_combo)

        self.status_combo = QComboBox()
        self.status_combo.addItem("Все статусы", None)
        self.status_combo.addItem("К отгрузке", AssignmentStatus.TO_SHIP)
        self.status_combo.addItem("На точке", AssignmentStatus.ON_POINT)
        self.status_combo.addItem("Выдана", AssignmentStatus.DELIVERED)
        self.status_combo.currentIndexChanged.connect(self.refresh_table)
        filters.addWidget(self.status_combo)
        bar_lay.addLayout(filters)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.count_label = QLabel("—")
        self.count_label.setObjectName("muted")
        actions.addWidget(self.count_label)
        actions.addStretch()

        btn_on_point = QPushButton("На точке")
        btn_on_point.clicked.connect(lambda: self._set_status(AssignmentStatus.ON_POINT))
        actions.addWidget(btn_on_point)

        btn_delivered = QPushButton("Выдана")
        btn_delivered.setObjectName("success")
        btn_delivered.clicked.connect(lambda: self._set_status(AssignmentStatus.DELIVERED))
        actions.addWidget(btn_delivered)

        btn_return = QPushButton("Вернуть в работу")
        btn_return.clicked.connect(lambda: self._set_status(AssignmentStatus.TO_SHIP))
        actions.addWidget(btn_return)
        bar_lay.addLayout(actions)

        layout.addWidget(bar)

        # --- Table ---
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Описание", "Номер отправления", "Клиент", "Точка", "Ячейка", "Статус"]
        )
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 6):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(40)
        layout.addWidget(self.table, 1)

        # --- Danger zone: сброс по точке ---
        danger = QFrame()
        danger.setObjectName("card")
        d_lay = QHBoxLayout(danger)
        d_lay.setContentsMargins(16, 12, 16, 12)
        d_lay.setSpacing(10)
        d_label = QLabel("Сброс базы посылок (удаляет посылки точки целиком):")
        d_label.setObjectName("subtitle")
        d_lay.addWidget(d_label)
        d_lay.addStretch()

        btn_reset_koms = QPushButton("Очистить Комсомольскую")
        btn_reset_koms.setObjectName("danger")
        btn_reset_koms.clicked.connect(lambda: self._reset_point(DeliveryPoint.KOMSOMOLSKAYA_4))
        d_lay.addWidget(btn_reset_koms)

        btn_reset_kolt = QPushButton("Очистить Кольцевую")
        btn_reset_kolt.setObjectName("danger")
        btn_reset_kolt.clicked.connect(lambda: self._reset_point(DeliveryPoint.KOLTSEVAYA_16))
        d_lay.addWidget(btn_reset_kolt)

        layout.addWidget(danger)

        self.refresh_table()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_table()

    def refresh_table(self):
        session = self.db_manager.get_session()
        try:
            svc = ShipmentControlService(session)
            shipments = svc.list_shipments(
                search=self.search.text(),
                point=self.point_combo.currentData(),
                status=self.status_combo.currentData(),
            )
            self.table.setRowCount(0)
            for sh in shipments:
                row = self.table.rowCount()
                self.table.insertRow(row)

                desc = sh.product_name or sh.product_label or sh.posting_number
                desc_item = QTableWidgetItem(desc)
                desc_item.setData(Qt.UserRole, sh.id)
                self.table.setItem(row, 0, desc_item)
                self.table.setItem(row, 1, QTableWidgetItem(sh.posting_number))
                self.table.setItem(row, 2, QTableWidgetItem(
                    sh.client.full_name if sh.client and sh.client.full_name
                    else (sh.ozon_client_id_raw or "")
                ))
                self.table.setItem(row, 3, QTableWidgetItem(
                    _POINT_LABEL.get(sh.assigned_point, "—")
                ))
                self.table.setItem(row, 4, QTableWidgetItem(sh.cell or ""))

                text, kind = _STATUS_BADGE.get(sh.assignment_status, ("—", "neutral"))
                self.table.setCellWidget(row, 5, make_badge(text, kind))

            self.count_label.setText(f"Показано: {len(shipments)}")
        finally:
            session.close()

    def _selected_shipment_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item else None

    def _set_status(self, new_status):
        ship_id = self._selected_shipment_id()
        if ship_id is None:
            QMessageBox.information(self, "Нет выбора", "Выберите посылку в списке.")
            return
        session = self.db_manager.get_session()
        try:
            ShipmentControlService(session).set_status(ship_id, new_status)
        finally:
            session.close()
        self.refresh_table()

    def _reset_point(self, point):
        session = self.db_manager.get_session()
        try:
            svc = ShipmentControlService(session)
            n = svc.count_for_point(point)
            if n == 0:
                QMessageBox.information(self, "Пусто",
                                       f"По точке «{_POINT_LABEL[point]}» посылок нет.")
                return
            if QMessageBox.question(
                self, "Сброс базы посылок",
                f"Удалить ВСЕ посылки точки «{_POINT_LABEL[point]}» — {n} шт.?\n"
                f"Действие необратимо.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            ) != QMessageBox.Yes:
                return
            deleted = svc.reset_point(point)
            QMessageBox.information(self, "Готово", f"Удалено посылок: {deleted}.")
        finally:
            session.close()
        self.refresh_table()
