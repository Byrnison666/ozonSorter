import html

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame,
)

from src.duplicate_service import DUPLICATE_DAYS, DuplicateService

from .stale_screen import _CellItem
from .sync_flow import ask_yes_no
from .theme import Colors, add_shadow, set_button_icon


# Даты — до «Товара»: в узком окне за край уходит правая колонка, и это должна
# быть «Точка» (она есть и в подсказке строки), а не даты, по которым решают.
_COLUMNS = [
    "Ячейка", "Номер отправления", "Клиент", "Выдана", "Снова пришла", "Товар", "Точка",
]
(_CELL_COL, _POSTING_COL, _CLIENT_COL, _DELIVERED_COL,
 _REAPPEARED_COL, _PRODUCT_COL, _POINT_COL) = range(len(_COLUMNS))
_CLIENT_WIDTH = 150


class DuplicatesScreen(QWidget):
    """Дубли: выданные посылки, которые Ozon снова прислал в отчёте склада. До
    решения оператора в отгрузку не идут."""

    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window
        self._rows = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        h1 = QLabel("Дубли")
        h1.setObjectName("h1")
        title_block.addWidget(h1)
        sub = QLabel(
            f"Посылки, которые уже выдали, а в отчёте склада они пришли снова — тот же "
            f"номер, штрихкод и клиент, не позже {DUPLICATE_DAYS} дней после выдачи. "
            f"В отгрузку они не попадут, пока вы не решите. «Отгрузить» — это новая "
            f"посылка: она войдёт в Excel по последнему отчёту, уже сформированный файл "
            f"сформируйте заново. «Ошибка Ozon» — посылку уже выдали, везти нечего."
        )
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        title_block.addWidget(sub)
        layout.addLayout(title_block)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.ship_btn = QPushButton("Отгрузить")
        self.ship_btn.setObjectName("primary")
        set_button_icon(self.ship_btn, "truck", Colors.ON_PRIMARY)
        self.ship_btn.clicked.connect(self.on_ship)
        actions.addWidget(self.ship_btn)
        self.dismiss_btn = QPushButton("Ошибка Ozon")
        self.dismiss_btn.setObjectName("danger")
        set_button_icon(self.dismiss_btn, "ban", Colors.DANGER_TEXT)
        self.dismiss_btn.clicked.connect(self.on_dismiss)
        actions.addWidget(self.dismiss_btn)
        self.selection_hint = QLabel("Выберите посылки в списке.")
        self.selection_hint.setObjectName("muted")
        actions.addSpacing(8)
        actions.addWidget(self.selection_hint)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.card = QFrame()
        self.card.setObjectName("card")
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(2, 2, 2, 2)
        card_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeaderItem(_DELIVERED_COL).setToolTip(
            "Последний отчёт, в котором посылка была до выдачи."
        )
        self.table.horizontalHeaderItem(_REAPPEARED_COL).setToolTip(
            "Отчёт, в котором посылка пришла снова."
        )
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        head.setSectionResizeMode(QHeaderView.ResizeToContents)
        head.setSectionResizeMode(_CLIENT_COL, QHeaderView.Interactive)
        head.resizeSection(_CLIENT_COL, _CLIENT_WIDTH)
        head.setSectionResizeMode(_PRODUCT_COL, QHeaderView.Stretch)  # «Товар» тянется
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.itemSelectionChanged.connect(self._update_actions)
        card_lay.addWidget(self.table)
        layout.addWidget(self.card, 1)
        add_shadow(self.card)

        self.empty_label = QLabel("")
        self.empty_label.setObjectName("subtitle")
        layout.addWidget(self.empty_label)
        # Забирает высоту, когда карточка с таблицей скрыта (пустое состояние).
        layout.addStretch()

        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        session = self.db_manager.get_session()
        try:
            self._rows = DuplicateService(session).list_duplicates()
        finally:
            session.close()

        self.empty_label.setText("Дублей в последнем отчёте нет.")
        self.empty_label.setVisible(not self._rows)
        self.card.setVisible(bool(self._rows))
        self._fill_table()
        self._update_actions()

    def _fill_table(self):
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            tooltip_text = (
                f"Товар: {row.product_name or '—'}\n"
                f"Клиент: {row.client_name or '—'} (Ozon ID {row.ozon_client_id or '—'})\n"
                f"Телефон: {row.phone or '—'}\n"
                f"Штрихкод: {row.product_label}\n"
                f"Точка: {row.point_label}"
            )
            # Подсказка рисуется как HTML, если похожа на него; значения — из базы.
            tooltip = "<p style='white-space:pre'>" + html.escape(tooltip_text) + "</p>"

            cell_item = _CellItem(row.cell)
            cell_font = cell_item.font()
            cell_font.setFamily("Onest Medium")
            cell_item.setFont(cell_font)
            product_item = QTableWidgetItem(row.product_name)
            product_item.setForeground(QColor(Colors.TEXT_SECONDARY))
            point_item = QTableWidgetItem(row.point_label)
            point_item.setForeground(QColor(Colors.TEXT_SECONDARY))

            items = {
                _CELL_COL: cell_item,
                _POSTING_COL: QTableWidgetItem(row.posting_number),
                _CLIENT_COL: QTableWidgetItem(row.client_name),
                _PRODUCT_COL: product_item,
                _POINT_COL: point_item,
                _DELIVERED_COL: QTableWidgetItem(row.delivered_label),
                _REAPPEARED_COL: QTableWidgetItem(row.reappeared_label),
            }
            for col, item in items.items():
                item.setToolTip(tooltip)
                self.table.setItem(r, col, item)

    def _selected_rows(self):
        indexes = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self._rows[i] for i in indexes]

    def _update_actions(self):
        selected = bool(self._selected_rows())
        self.ship_btn.setEnabled(selected)
        self.dismiss_btn.setEnabled(selected)
        self.selection_hint.setVisible(bool(self._rows) and not selected)

    def on_ship(self):
        self._decide(
            "Отгрузить",
            "Это настоящие новые посылки? Они войдут в Excel отгрузки по последнему "
            "отчёту — если файл для точки уже сформирован, сформируйте его заново.",
            DuplicateService.ship,
        )

    def on_dismiss(self):
        self._decide(
            "Ошибка Ozon",
            "Посылки уже выдали, везти нечего? В отгрузку они не попадут. Если "
            f"пропадут из отчёта и вернутся в течение {DUPLICATE_DAYS} дней — снова "
            "станут дублями.",
            DuplicateService.dismiss,
        )

    def _decide(self, title, question, action):
        rows = self._selected_rows()
        if not rows:
            return
        numbers = "\n".join(row.posting_number for row in rows[:10])
        if len(rows) > 10:
            numbers += f"\n… и ещё {len(rows) - 10}"
        if not ask_yes_no(self, title, f"{numbers}\n\n{question}", default_yes=False):
            return

        session = self.db_manager.get_session()
        try:
            service = DuplicateService(session)
            for row in rows:
                action(service, row.shipment_id)
        finally:
            session.close()
        self.refresh()
        # Последним действием: синхронизация может заменить базу и пересоздать
        # этот экран.
        self.main_window.on_data_changed()
