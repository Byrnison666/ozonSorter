from datetime import date, datetime

from PySide2.QtCore import Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox,
    QFileDialog, QMessageBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame,
)

from src.export_service import natural_key
from src.stale_service import (
    DANGER_DAYS, POINT_LABELS, WARN_DAYS, StaleShipmentService,
)

from .theme import Colors, make_badge, make_stat_tile


# На экране — сокращённый набор колонок: все 11 из Excel-выгрузки в окно не
# помещаются. Штрихкод, Ozon ID, телефон и дата первой встречи — в подсказке
# строки и в Excel.
# «Статус» стоит сразу за ячейкой: в узком окне за край уходит правая колонка,
# и это должна быть «Точка» (её заменяет фильтр), а не пометка «Возврат».
_COLUMNS = [
    "Дней в базе", "Ячейка", "Статус", "Номер отправления", "Клиент", "Товар", "Точка",
]
(_DAYS_COL, _CELL_COL, _STATUS_COL, _POSTING_COL,
 _CLIENT_COL, _PRODUCT_COL, _POINT_COL) = range(len(_COLUMNS))
_DAYS_WIDTH = 112
_CLIENT_WIDTH = 150
_MIN_DAYS_PRESETS = [0, WARN_DAYS, DANGER_DAYS, 14]


class _DaysDelegate(QStyledItemDelegate):
    """QSS из theme.py (QTableWidget::item) игнорирует BackgroundRole ячейки и
    закрашивает её фоном выделения/наведения. Поэтому заливку рисуем сами, а
    эти состояния у подсвеченной ячейки гасим — срок виден и в выбранной строке."""

    def paint(self, painter, option, index):
        brush = index.data(Qt.BackgroundRole)
        if brush is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        opt.state = opt.state & ~QStyle.State_Selected & ~QStyle.State_MouseOver
        painter.fillRect(option.rect, brush)
        super().paint(painter, opt, index)


class _CellItem(QTableWidgetItem):
    """Ячейки сортируются натурально («A-2» раньше «A-10»), а не как текст."""

    def __lt__(self, other):
        return natural_key(self.text()) < natural_key(other.text())


class StaleScreen(QWidget):
    """Залежавшиеся посылки: наши посылки, которые до сих пор висят в последнем
    загруженном отчёте склада, и сколько дней прошло с их первого появления.
    Помогает найти забытые — клиент не пришёл либо выдали без отметки в базе."""

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
        h1 = QLabel("Залежавшиеся посылки")
        h1.setObjectName("h1")
        title_block.addWidget(h1)
        sub = QLabel(
            "Эти посылки всё ещё числятся на складе. Проверьте по ячейкам: "
            "клиент не пришёл, либо посылку выдали и не отметили в базе."
        )
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        title_block.addWidget(sub)

        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        self.import_info_label = QLabel("")
        self.import_info_label.setObjectName("subtitle")
        info_row.addWidget(self.import_info_label)
        self.outdated_badge = make_badge(
            "отчёт не сегодняшний — загрузите свежий", "warning"
        )
        info_row.addWidget(self.outdated_badge)
        info_row.addStretch(1)
        title_block.addLayout(info_row)
        layout.addLayout(title_block)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        _, self.tile_total = make_stat_tile(tiles, "Показано", "0")
        _, self.tile_warn = make_stat_tile(
            tiles, f"{WARN_DAYS}–{DANGER_DAYS - 1} дней", "0"
        )
        _, self.tile_danger = make_stat_tile(tiles, f"От {DANGER_DAYS} дней", "0")
        layout.addLayout(tiles)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        point_lbl = QLabel("Точка:")
        point_lbl.setObjectName("subtitle")
        filters.addWidget(point_lbl)
        self.point_combo = QComboBox()
        self.point_combo.addItem("Все точки", None)
        for point, label in POINT_LABELS.items():
            self.point_combo.addItem(label, point)
        self.point_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.point_combo)

        filters.addSpacing(8)
        days_lbl = QLabel("Срок:")
        days_lbl.setObjectName("subtitle")
        filters.addWidget(days_lbl)
        self.min_days_combo = QComboBox()
        for days in _MIN_DAYS_PRESETS:
            self.min_days_combo.addItem(f"От {days} дней" if days else "Любой срок", days)
        self.min_days_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.min_days_combo)

        filters.addStretch(1)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.refresh)
        filters.addWidget(self.refresh_btn)
        self.export_btn = QPushButton("Выгрузить в Excel")
        self.export_btn.setObjectName("primary")
        self.export_btn.clicked.connect(self.on_export)
        filters.addWidget(self.export_btn)
        layout.addLayout(filters)

        self.card = QFrame()
        self.card.setObjectName("card")
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(2, 2, 2, 2)
        card_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setItemDelegateForColumn(_DAYS_COL, _DaysDelegate(self.table))
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        days_header = self.table.horizontalHeaderItem(_DAYS_COL)
        days_header.setTextAlignment(Qt.AlignCenter)
        days_header.setToolTip(
            "Сколько дней прошло с первого появления посылки в загруженных отчётах.\n"
            f"От {WARN_DAYS} дней — жёлтый, от {DANGER_DAYS} дней — красный."
        )
        head.setSectionResizeMode(QHeaderView.ResizeToContents)
        # По содержимому колонка дней вышла бы шире: Qt резервирует место под
        # стрелку сортировки рядом с длинным заголовком.
        head.setSectionResizeMode(_DAYS_COL, QHeaderView.Fixed)
        head.resizeSection(_DAYS_COL, _DAYS_WIDTH)
        head.setSectionResizeMode(_CLIENT_COL, QHeaderView.Interactive)
        head.resizeSection(_CLIENT_COL, _CLIENT_WIDTH)
        head.setSectionResizeMode(_PRODUCT_COL, QHeaderView.Stretch)  # «Товар» тянется
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(38)
        head.setSortIndicator(_DAYS_COL, Qt.DescendingOrder)
        self.table.setSortingEnabled(True)
        card_lay.addWidget(self.table)
        layout.addWidget(self.card, 1)

        self.empty_label = QLabel("")
        self.empty_label.setObjectName("subtitle")
        layout.addWidget(self.empty_label)
        # Забирает высоту, когда карточка с таблицей скрыта (пустое состояние).
        layout.addStretch()

        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self, *_args):
        point = self.point_combo.currentData()
        min_days = self.min_days_combo.currentData()

        session = self.db_manager.get_session()
        try:
            service = StaleShipmentService(session)
            latest = service.latest_import()
            if latest is not None:
                info = (
                    f"По отчёту от {latest.started_at.strftime('%d.%m.%Y %H:%M')}"
                    f" — {latest.source_file_name or '—'}"
                )
                outdated = latest.started_at.date() < date.today()
            else:
                info = ""
                outdated = False
            rows = service.list_stale(point=point, min_days=min_days)
        finally:
            session.close()

        self._rows = rows
        self.import_info_label.setText(info)
        self.import_info_label.setVisible(latest is not None)
        self.outdated_badge.setVisible(outdated)
        self.tile_total.setText(str(len(rows)))
        self.tile_warn.setText(
            str(sum(1 for r in rows if WARN_DAYS <= r.days < DANGER_DAYS))
        )
        self.tile_danger.setText(str(sum(1 for r in rows if r.days >= DANGER_DAYS)))

        if latest is None:
            empty_text = "Отчёт ещё не загружен. Импортируйте его на «Главной»."
        elif point is not None or min_days:
            empty_text = "Нет посылок по выбранным фильтрам."
        else:
            empty_text = "Залежавшихся посылок нет."
        self.empty_label.setText(empty_text)
        self.empty_label.setVisible(not rows)
        self.card.setVisible(bool(rows))
        self.export_btn.setEnabled(bool(rows))

        # Когда выбрана одна точка, колонка «Точка» у всех строк одинакова.
        self.table.setColumnHidden(_POINT_COL, point is not None)
        self._fill_table(rows)

    def _fill_table(self, rows):
        # Сортировку на время заполнения выключаем, иначе строки перемешиваются
        # прямо во время вставки.
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            tooltip = (
                f"Товар: {row.product_name or '—'}\n"
                f"Клиент: {row.client_name or '—'} (Ozon ID {row.ozon_client_id})\n"
                f"Телефон: {row.phone or '—'}\n"
                f"Штрихкод: {row.product_label}\n"
                f"Впервые в отчёте: {row.first_seen_label}"
            )

            days_item = QTableWidgetItem()
            # Число, а не строка — чтобы сортировка по заголовку была числовой.
            days_item.setData(Qt.DisplayRole, row.days)
            days_item.setTextAlignment(Qt.AlignCenter)
            if row.days >= DANGER_DAYS:
                self._emphasize(days_item, Colors.DANGER_TEXT)
                days_item.setBackground(QColor(Colors.DANGER_SOFT))
            elif row.days >= WARN_DAYS:
                self._emphasize(days_item, Colors.WARNING_TEXT)
                days_item.setBackground(QColor(Colors.WARNING_SOFT))

            status_item = QTableWidgetItem("Возврат" if row.is_returned else "")
            if row.is_returned:
                self._emphasize(status_item, Colors.DANGER_TEXT)

            items = {
                _DAYS_COL: days_item,
                _CELL_COL: _CellItem(row.cell),
                _POSTING_COL: QTableWidgetItem(row.posting_number),
                _CLIENT_COL: QTableWidgetItem(row.client_name),
                _PRODUCT_COL: QTableWidgetItem(row.product_name),
                _POINT_COL: QTableWidgetItem(row.point_label),
                _STATUS_COL: status_item,
            }
            for col, item in items.items():
                item.setToolTip(tooltip)
                self.table.setItem(r, col, item)
        self.table.setSortingEnabled(True)

    @staticmethod
    def _emphasize(item, color):
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(color))

    def on_export(self):
        default_name = f"Залежавшиеся_{datetime.now().strftime('%d.%m.%Y')}.xlsx"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить список", default_name, "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        try:
            StaleShipmentService.export_xlsx(self._rows, file_path)
            QMessageBox.information(self, "Готово", f"Файл сохранён:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта",
                                 f"Не удалось сохранить файл:\n{e}")
