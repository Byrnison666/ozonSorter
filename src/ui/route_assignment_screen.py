from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QAbstractItemView, QFrame
)
from sqlalchemy import select, func

from src.models import Shipment, Client, AssignmentStatus, DeliveryPoint
from .theme import make_badge


class RouteAssignmentScreen(QWidget):
    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        # Header
        header_block = QVBoxLayout()
        header_block.setSpacing(4)
        h1 = QLabel("Распределение")
        h1.setObjectName("h1")
        header_block.addWidget(h1)
        sub = QLabel(
            "Посылки клиентов с политикой MANUAL ждут выбора точки. "
            "Без распределения файлы отгрузки не формируются."
        )
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        header_block.addWidget(sub)
        layout.addLayout(header_block)

        # Status card
        self.status_card = QFrame()
        self.status_card.setObjectName("card")
        status_lay = QHBoxLayout(self.status_card)
        status_lay.setContentsMargins(20, 16, 20, 16)
        status_lay.setSpacing(14)

        self.status_badge = make_badge("—", "neutral")
        status_lay.addWidget(self.status_badge, 0, Qt.AlignVCenter)

        self.status_text = QLabel("—")
        self.status_text.setObjectName("subtitle")
        self.status_text.setWordWrap(True)
        status_lay.addWidget(self.status_text, 1)

        layout.addWidget(self.status_card)

        # Action toolbar
        action_card = QFrame()
        action_card.setObjectName("card")
        action_lay = QVBoxLayout(action_card)
        action_lay.setContentsMargins(20, 14, 20, 14)
        action_lay.setSpacing(10)

        actions_top = QHBoxLayout()
        actions_top.setSpacing(8)
        actions_top.addWidget(self._action_label("Выделенные строки →"))
        self.btn_to_koms = QPushButton("Комсомольская 4")
        self.btn_to_koms.setObjectName("primary")
        self.btn_to_koms.clicked.connect(
            lambda: self.assign_selected(DeliveryPoint.KOMSOMOLSKAYA_4)
        )
        actions_top.addWidget(self.btn_to_koms)

        self.btn_to_kolt = QPushButton("Кольцевая 16")
        self.btn_to_kolt.setObjectName("primary")
        self.btn_to_kolt.clicked.connect(
            lambda: self.assign_selected(DeliveryPoint.KOLTSEVAYA_16)
        )
        actions_top.addWidget(self.btn_to_kolt)

        actions_top.addSpacing(20)
        actions_top.addWidget(self._action_label("Массово →"))
        self.btn_all_koms = QPushButton("Все на Комсомольскую")
        self.btn_all_koms.clicked.connect(
            lambda: self.assign_all(DeliveryPoint.KOMSOMOLSKAYA_4)
        )
        actions_top.addWidget(self.btn_all_koms)

        self.btn_all_kolt = QPushButton("Все на Кольцевую")
        self.btn_all_kolt.clicked.connect(
            lambda: self.assign_all(DeliveryPoint.KOLTSEVAYA_16)
        )
        actions_top.addWidget(self.btn_all_kolt)

        actions_top.addStretch()

        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.clicked.connect(self.refresh)
        actions_top.addWidget(self.btn_refresh)

        action_lay.addLayout(actions_top)

        layout.addWidget(action_card)

        # Table card
        table_card = QFrame()
        table_card.setObjectName("card")
        table_lay = QVBoxLayout(table_card)
        table_lay.setContentsMargins(2, 2, 2, 2)
        table_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Номер отправления", "Ozon ID", "ФИО клиента",
            "Товар", "Ячейка", "Дата"
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

        self.refresh()

    @staticmethod
    def _action_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        session = self.db_manager.get_session()
        try:
            stmt = (
                select(Shipment, Client)
                .join(Client, Shipment.client_id == Client.id, isouter=True)
                .where(Shipment.assignment_status == AssignmentStatus.TO_ASSIGN)
                .order_by(Shipment.ozon_client_id_raw, Shipment.posting_number)
            )
            rows = session.execute(stmt).all()

            self.table.setRowCount(0)
            for shipment, client in rows:
                r = self.table.rowCount()
                self.table.insertRow(r)
                self._set_cell(r, 0, shipment.posting_number, shipment.id)
                self._set_cell(r, 1, shipment.ozon_client_id_raw or "")
                self._set_cell(r, 2, (client.full_name if client else "") or "—")
                self._set_cell(r, 3,
                               shipment.product_name or shipment.product_label or "")
                self._set_cell(r, 4, shipment.cell or "")
                self._set_cell(
                    r, 5,
                    shipment.shipment_date_ozon.strftime("%d.%m.%Y")
                    if shipment.shipment_date_ozon else ""
                )

            total_to_assign = self.table.rowCount()
            total_to_ship = session.execute(
                select(func.count(Shipment.id)).where(
                    Shipment.assignment_status == AssignmentStatus.TO_SHIP,
                    Shipment.assigned_point.is_not(None),
                )
            ).scalar() or 0

            self._update_status(total_to_assign, total_to_ship)

            for btn in (self.btn_to_koms, self.btn_to_kolt,
                        self.btn_all_koms, self.btn_all_kolt):
                btn.setEnabled(total_to_assign > 0)
        finally:
            session.close()

    def _update_status(self, to_assign: int, to_ship: int):
        # Replace badge widget
        parent_layout = self.status_card.layout()
        parent_layout.removeWidget(self.status_badge)
        self.status_badge.deleteLater()

        if to_assign == 0:
            self.status_badge = make_badge("Всё распределено", "success")
            self.status_text.setText(
                f"Ожидают выбора точки: 0. "
                f"В плане отгрузки уже {to_ship} посылок."
            )
        else:
            self.status_badge = make_badge(
                f"{to_assign} в очереди", "warning"
            )
            self.status_text.setText(
                f"Распределите посылки по точкам. "
                f"В плане отгрузки уже {to_ship}."
            )
        parent_layout.insertWidget(0, self.status_badge, 0, Qt.AlignVCenter)

    def _set_cell(self, row, col, text, shipment_id=None):
        item = QTableWidgetItem(text)
        if shipment_id is not None:
            item.setData(Qt.UserRole, shipment_id)
        self.table.setItem(row, col, item)

    def _selected_shipment_ids(self):
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                sid = item.data(Qt.UserRole)
                if sid is not None:
                    ids.append(int(sid))
        return ids

    def assign_selected(self, point: DeliveryPoint):
        ids = self._selected_shipment_ids()
        if not ids:
            QMessageBox.information(
                self, "Нет выбранных",
                "Выделите хотя бы одну строку в таблице."
            )
            return
        self._apply_assignment(ids, point)

    def assign_all(self, point: DeliveryPoint):
        session = self.db_manager.get_session()
        try:
            ids = session.execute(
                select(Shipment.id).where(
                    Shipment.assignment_status == AssignmentStatus.TO_ASSIGN
                )
            ).scalars().all()
        finally:
            session.close()
        if not ids:
            return
        confirm = QMessageBox.question(
            self, "Подтверждение",
            f"Отправить ВСЕ {len(ids)} посылок на "
            f"{self._point_title(point)}?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return
        self._apply_assignment(list(ids), point)

    def _apply_assignment(self, ids, point: DeliveryPoint):
        session = self.db_manager.get_session()
        try:
            shipments = session.execute(
                select(Shipment).where(Shipment.id.in_(ids))
            ).scalars().all()
            count = 0
            for sh in shipments:
                if sh.assignment_status != AssignmentStatus.TO_ASSIGN:
                    continue
                sh.assigned_point = point
                sh.assignment_status = AssignmentStatus.TO_SHIP
                count += 1
            session.commit()
        except Exception as e:
            session.rollback()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
            return
        finally:
            session.close()
        QMessageBox.information(
            self, "Готово",
            f"Распределено {count} посылок на {self._point_title(point)}."
        )
        self.refresh()

    @staticmethod
    def _point_title(point: DeliveryPoint) -> str:
        return ("Комсомольскую 4"
                if point == DeliveryPoint.KOMSOMOLSKAYA_4
                else "Кольцевую 16")
