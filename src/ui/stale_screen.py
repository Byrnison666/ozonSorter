import html
from datetime import date, datetime

from PySide2.QtCore import QRectF, Qt
from PySide2.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QButtonGroup, QGridLayout,
    QFileDialog, QMessageBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame,
)

from src.export_service import natural_key
from src.stale_service import (
    ALERT_DAYS, DANGER_DAYS, POINT_LABELS, RETURN_DAYS, WARN_DAYS, StaleShipmentService,
)

from .theme import (
    Colors, add_shadow, icon_label, make_badge, make_overline, make_stat_tile, set_button_icon,
)


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
_MIN_DAYS_PRESETS = [0, WARN_DAYS, DANGER_DAYS, ALERT_DAYS, RETURN_DAYS]


def _days_colors(days: int):
    """(фон, текст) пилюли срока: от DANGER_DAYS — красная, от WARN_DAYS — жёлтая."""
    if days >= DANGER_DAYS:
        return Colors.DANGER_SOFT, Colors.DANGER_TEXT
    if days >= WARN_DAYS:
        return Colors.WARNING_SOFT, Colors.WARNING_TEXT
    return Colors.SURFACE_ALT, Colors.TEXT_SECONDARY


class _PillDelegate(QStyledItemDelegate):
    """Значение ячейки — цветной пилюлей (как Badge в макете). Строку выделения
    и наведения рисует стиль как обычно, пилюля поверх неё."""

    _HEIGHT = 22
    _DOT = 6

    def __init__(self, parent, colors, text, dot=False):
        super().__init__(parent)
        self._colors = colors  # значение → (фон, текст) или None — без пилюли
        self._text = text      # значение → подпись
        self._dot = dot        # точка цвета текста перед подписью

    def paint(self, painter, option, index):
        value = index.data(Qt.DisplayRole)
        colors = self._colors(value) if value not in (None, "") else None
        if colors is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        background, color = colors
        text = self._text(value)
        font = QFont(option.font)
        font.setFamilies(["Onest Medium", "Segoe UI"])
        font.setFamily("Onest Medium")
        font.setPixelSize(12)
        dot = self._DOT + 6 if self._dot else 0
        width = QFontMetrics(font).horizontalAdvance(text) + 20 + dot
        rect = QRectF(
            option.rect.left() + 12,
            option.rect.top() + (option.rect.height() - self._HEIGHT) / 2,
            width, self._HEIGHT,
        )
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, self._HEIGHT / 2, self._HEIGHT / 2)
        if dot:
            painter.setBrush(QColor(color))
            painter.drawEllipse(QRectF(
                rect.left() + 10, rect.center().y() - self._DOT / 2, self._DOT, self._DOT
            ))
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect.adjusted(dot, 0, 0, 0), Qt.AlignCenter, text)
        painter.restore()


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
        info_row.setSpacing(6)
        self.import_info_icon = icon_label("calendar", Colors.TEXT_MUTED, 14, 14)
        info_row.addWidget(self.import_info_icon)
        self.import_info_label = QLabel("")
        self.import_info_label.setObjectName("muted")
        # Имя файла берётся из базы, которая могла прийти с Яндекс.Диска: как
        # HTML его показывать нельзя (<img src="file://…"> Qt открыл бы сам).
        self.import_info_label.setTextFormat(Qt.PlainText)
        info_row.addWidget(self.import_info_label)
        self.outdated_badge = make_badge(
            "отчёт не сегодняшний — загрузите свежий", "warning"
        )
        info_row.addSpacing(4)
        info_row.addWidget(self.outdated_badge)
        info_row.addStretch(1)
        title_block.addSpacing(4)
        title_block.addLayout(info_row)
        layout.addLayout(title_block)

        filters_card = QFrame()
        filters_card.setObjectName("card")
        filters = QHBoxLayout(filters_card)
        filters.setContentsMargins(16, 16, 16, 16)
        filters.setSpacing(16)
        chips = QGridLayout()
        chips.setHorizontalSpacing(12)
        chips.setVerticalSpacing(12)
        point_row, self.point_group, self._point_chips = self._chip_row(
            [(None, "Все точки")] + list(POINT_LABELS.items())
        )
        chips.addWidget(make_overline("Точка"), 0, 0, Qt.AlignVCenter)
        chips.addLayout(point_row, 0, 1)
        days_row, self.min_days_group, self._min_days_chips = self._chip_row(
            [(days, f"От {days} дней" if days else "Любой срок") for days in _MIN_DAYS_PRESETS]
        )
        chips.addWidget(make_overline("Срок"), 1, 0, Qt.AlignVCenter)
        chips.addLayout(days_row, 1, 1)
        filters.addLayout(chips, 1)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        self.export_btn = QPushButton("Выгрузить в Excel")
        self.export_btn.setObjectName("primary")
        set_button_icon(self.export_btn, "download", Colors.ON_PRIMARY)
        self.export_btn.clicked.connect(self.on_export)
        actions.addWidget(self.export_btn)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.setObjectName("secondary")
        set_button_icon(self.refresh_btn, "refresh-cw", Colors.TEXT)
        self.refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_btn)
        filters.addLayout(actions)
        layout.addWidget(filters_card)
        add_shadow(filters_card)

        tiles = QHBoxLayout()
        tiles.setSpacing(16)
        tile, self.tile_total = make_stat_tile("Показано", "0")
        tiles.addWidget(tile, 1)
        tile, self.tile_warn = make_stat_tile(
            f"{WARN_DAYS}–{DANGER_DAYS - 1} дней", "0", tone="warning"
        )
        tiles.addWidget(tile, 1)
        tile, self.tile_danger = make_stat_tile(f"От {DANGER_DAYS} дней", "0", tone="danger")
        tiles.addWidget(tile, 1)
        layout.addLayout(tiles)

        self.card = QFrame()
        self.card.setObjectName("card")
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(2, 2, 2, 2)
        card_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setItemDelegateForColumn(
            _DAYS_COL, _PillDelegate(self.table, _days_colors, lambda days: f"{days} дн.")
        )
        self.table.setItemDelegateForColumn(_STATUS_COL, _PillDelegate(
            self.table, lambda _status: (Colors.DANGER_SOFT, Colors.DANGER_TEXT), str, dot=True
        ))
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        days_header = self.table.horizontalHeaderItem(_DAYS_COL)
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
        self.table.verticalHeader().setDefaultSectionSize(48)
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

    def _chip_row(self, options):
        """Ряд чипов-переключателей (Chip в макете). Возвращает (ряд, группа,
        {значение: чип}); выбран первый."""
        row = QHBoxLayout()
        row.setSpacing(8)
        group = QButtonGroup(self)
        chips = {}
        for value, label in options:
            chip = QPushButton(label)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            group.addButton(chip)
            row.addWidget(chip)
            chips[value] = chip
        row.addStretch(1)
        next(iter(chips.values())).setChecked(True)
        group.buttonClicked.connect(self.refresh)
        return row, group, chips

    @staticmethod
    def _checked_value(chips):
        return next(value for value, chip in chips.items() if chip.isChecked())

    def set_min_days(self, days: int):
        """Выбрать фильтр срока (переход с плашки «Скоро возврат» на «Главной»)."""
        chip = self._min_days_chips.get(days)
        if chip is not None and not chip.isChecked():
            chip.setChecked(True)
            self.refresh()

    def refresh(self, *_args):
        point = self._checked_value(self._point_chips)
        min_days = self._checked_value(self._min_days_chips)

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
        self.import_info_icon.setVisible(latest is not None)
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
            tooltip_text = (
                f"Товар: {row.product_name or '—'}\n"
                f"Клиент: {row.client_name or '—'} (Ozon ID {row.ozon_client_id})\n"
                f"Телефон: {row.phone or '—'}\n"
                f"Штрихкод: {row.product_label}\n"
                f"Впервые в отчёте: {row.first_seen_label}"
            )
            # Подсказка рисуется как HTML, если похожа на него; значения — из базы.
            tooltip = "<p style='white-space:pre'>" + html.escape(tooltip_text) + "</p>"

            days_item = QTableWidgetItem()
            # Число, а не строка — чтобы сортировка по заголовку была числовой.
            # Пилюлю «N дн.» рисует _PillDelegate.
            days_item.setData(Qt.DisplayRole, row.days)

            status_item = QTableWidgetItem("Возврат" if row.is_returned else "")

            cell_item = _CellItem(row.cell)
            cell_font = cell_item.font()
            cell_font.setFamily("Onest Medium")
            cell_item.setFont(cell_font)
            product_item = QTableWidgetItem(row.product_name)
            product_item.setForeground(QColor(Colors.TEXT_SECONDARY))
            point_item = QTableWidgetItem(row.point_label)
            point_item.setForeground(QColor(Colors.TEXT_SECONDARY))
            point_font = point_item.font()
            point_font.setPixelSize(12)
            point_item.setFont(point_font)

            items = {
                _DAYS_COL: days_item,
                _CELL_COL: cell_item,
                _POSTING_COL: QTableWidgetItem(row.posting_number),
                _CLIENT_COL: QTableWidgetItem(row.client_name),
                _PRODUCT_COL: product_item,
                _POINT_COL: point_item,
                _STATUS_COL: status_item,
            }
            for col, item in items.items():
                item.setToolTip(tooltip)
                self.table.setItem(r, col, item)
        self.table.setSortingEnabled(True)

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
