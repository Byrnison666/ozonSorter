from datetime import datetime

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QMessageBox, QFrame, QGridLayout
)

from src.export_service import ExportService
from src.models import DeliveryPoint
from src.parser import ExcelParser
from src.services import ImportService
from src.stale_service import StaleShipmentService, return_alert_text

from .sync_flow import ask_yes_no
from .theme import Colors, add_shadow, icon_label, make_stat_tile, set_button_icon


class DashboardScreen(QWidget):
    def __init__(self, db_manager, main_window):
        super().__init__()
        self.db_manager = db_manager
        self.main_window = main_window
        self.import_service = ImportService(self.db_manager.get_session())
        self.export_service = ExportService(self.db_manager.get_session())
        self.last_import_session_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        # --- Header ---
        header = QVBoxLayout()
        header.setSpacing(4)
        h1 = QLabel("Главная")
        h1.setObjectName("h1")
        header.addWidget(h1)

        sub = QLabel("Импорт отчёта от Казакова 68, проверка и формирование двух Excel.")
        sub.setObjectName("subtitle")
        header.addWidget(sub)

        layout.addLayout(header)

        # --- «Скоро возврат»: наши посылки, которые Ozon скоро может вернуть ---
        self.alert_banner = QFrame()
        self.alert_banner.setObjectName("alertBanner")
        alert_layout = QHBoxLayout(self.alert_banner)
        alert_layout.setContentsMargins(16, 14, 16, 14)
        alert_layout.setSpacing(16)
        alert_layout.addWidget(
            icon_label("triangle-alert", Colors.ON_PRIMARY, 20, 40, "alertIcon"), 0, Qt.AlignVCenter
        )
        alert_texts = QVBoxLayout()
        alert_texts.setSpacing(4)
        alert_title = QLabel("Скоро возврат")
        alert_title.setObjectName("alertTitle")
        alert_texts.addWidget(alert_title)
        self.alert_label = QLabel("")
        self.alert_label.setObjectName("alertText")
        self.alert_label.setWordWrap(True)
        alert_texts.addWidget(self.alert_label)
        alert_layout.addLayout(alert_texts, 1)
        alert_btn = QPushButton("Показать посылки")
        alert_btn.setObjectName("secondary")
        set_button_icon(alert_btn, "arrow-right", Colors.TEXT)
        alert_btn.clicked.connect(self.main_window.show_return_alert)
        alert_layout.addWidget(alert_btn, 0, Qt.AlignVCenter)
        layout.addWidget(self.alert_banner)
        self.alert_banner.setVisible(False)

        work_row = QHBoxLayout()
        work_row.setSpacing(20)

        # --- Drop zone card ---
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setMinimumHeight(260)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setContentsMargins(48, 28, 48, 28)
        drop_layout.setSpacing(10)
        drop_layout.addStretch()

        drop_layout.addWidget(
            icon_label("upload", Colors.PRIMARY, 24, 52, "dropIcon"), 0, Qt.AlignHCenter
        )
        drop_layout.addSpacing(6)

        drop_title = QLabel("Загрузите файл от Казакова 68")
        drop_title.setObjectName("h2")
        drop_title.setAlignment(Qt.AlignCenter)
        drop_layout.addWidget(drop_title)

        drop_hint = QLabel(
            "Excel-отчёт «Остатки на складе ДОНЕЦК_26». "
            "Программа сама найдёт ваших клиентов и исключит уже привезённые посылки."
        )
        drop_hint.setObjectName("subtitle")
        drop_hint.setAlignment(Qt.AlignCenter)
        drop_hint.setWordWrap(True)
        drop_layout.addWidget(drop_hint)
        drop_layout.addSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignCenter)
        self.import_btn = QPushButton("Выбрать файл…")
        self.import_btn.setObjectName("primary")
        set_button_icon(self.import_btn, "file-spreadsheet", Colors.ON_PRIMARY)
        self.import_btn.clicked.connect(self.on_import)
        btn_row.addWidget(self.import_btn)
        drop_layout.addLayout(btn_row)
        drop_layout.addStretch()

        work_row.addWidget(self.drop_zone, 1)

        # --- Export card ---
        self.export_card = QFrame()
        self.export_card.setObjectName("card")
        self.export_card.setFixedWidth(332)
        export_layout = QVBoxLayout(self.export_card)
        export_layout.setContentsMargins(24, 24, 24, 24)
        export_layout.setSpacing(12)

        exp_title = QLabel("Формирование отгрузки")
        exp_title.setObjectName("h2")
        export_layout.addWidget(exp_title)

        exp_sub = QLabel(
            "Два независимых Excel — отдельно для каждой точки. "
            "Папка сохранения запоминается."
        )
        exp_sub.setObjectName("subtitle")
        exp_sub.setWordWrap(True)
        export_layout.addWidget(exp_sub)
        export_layout.addStretch()

        self.export_koms_btn = QPushButton("Excel для Комсомольской 4")
        self.export_koms_btn.setObjectName("secondary")
        self.export_koms_btn.setMinimumHeight(44)
        set_button_icon(self.export_koms_btn, "download", Colors.PRIMARY)
        self.export_koms_btn.clicked.connect(
            lambda: self.on_export(DeliveryPoint.KOMSOMOLSKAYA_4)
        )
        export_layout.addWidget(self.export_koms_btn)

        self.export_kolt_btn = QPushButton("Excel для Кольцевой 16")
        self.export_kolt_btn.setObjectName("secondary")
        self.export_kolt_btn.setMinimumHeight(44)
        set_button_icon(self.export_kolt_btn, "download", Colors.PRIMARY)
        self.export_kolt_btn.clicked.connect(
            lambda: self.on_export(DeliveryPoint.KOLTSEVAYA_16)
        )
        export_layout.addWidget(self.export_kolt_btn)

        work_row.addWidget(self.export_card)
        add_shadow(self.export_card)
        self.export_card.setVisible(False)
        layout.addLayout(work_row)

        # --- Stats grid (hidden until import) ---
        self.stats_card = QFrame()
        self.stats_card.setObjectName("card")
        stats_layout = QVBoxLayout(self.stats_card)
        stats_layout.setContentsMargins(24, 22, 24, 24)
        stats_layout.setSpacing(16)

        stats_head = QHBoxLayout()
        stats_title = QLabel("Результаты последнего импорта")
        stats_title.setObjectName("h2")
        stats_head.addWidget(stats_title)
        stats_head.addStretch()
        file_pill = QFrame()
        file_pill.setObjectName("filePill")
        pill_layout = QHBoxLayout(file_pill)
        pill_layout.setContentsMargins(10, 5, 12, 5)
        pill_layout.setSpacing(6)
        pill_layout.addWidget(icon_label("file-spreadsheet", Colors.TEXT_MUTED, 14, 14))
        self.file_name_label = QLabel("—")
        self.file_name_label.setObjectName("muted")
        pill_layout.addWidget(self.file_name_label)
        stats_head.addWidget(file_pill)
        stats_layout.addLayout(stats_head)

        tiles_layout = QHBoxLayout()
        tiles_layout.setSpacing(12)
        hero, self.tile_new = make_stat_tile("Новых к отгрузке", "0", tone="hero")
        hero.setFixedWidth(220)
        tiles_layout.addWidget(hero)
        grid = QGridLayout()
        grid.setSpacing(12)
        _, self.tile_total = self._add_tile(grid, 0, 0, "Всего строк")
        _, self.tile_matched = self._add_tile(grid, 0, 1, "Наших")
        _, self.tile_on_point = self._add_tile(grid, 0, 2, "Уже на точках")
        _, self.tile_returned = self._add_tile(grid, 1, 0, "Возвраты")
        _, self.tile_not_ours = self._add_tile(grid, 1, 1, "Не наши")
        _, self.tile_kty = self._add_tile(grid, 1, 2, "КТЯ")
        tiles_layout.addLayout(grid, 1)
        stats_layout.addLayout(tiles_layout)

        layout.addWidget(self.stats_card)
        add_shadow(self.stats_card)
        self.stats_card.setVisible(False)

        layout.addStretch()

    @staticmethod
    def _add_tile(grid, row, column, label_text):
        frame, value = make_stat_tile(label_text, "0")
        grid.addWidget(frame, row, column)
        return frame, value

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_return_alert()

    def refresh_return_alert(self):
        """Показать плашку «Скоро возврат». Возвращает её текст ('' — нечего)."""
        session = self.db_manager.get_session()
        try:
            alert, overdue = StaleShipmentService(session).return_alert()
        finally:
            session.close()
        text = return_alert_text(alert, overdue) if alert else ""
        self.alert_label.setText(text)
        self.alert_banner.setVisible(bool(alert))
        return text

    def on_import(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите отчёт Ozon", "", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        dup = self.import_service.find_duplicate_import(file_path)
        if dup is not None:
            when = dup.started_at.strftime("%d.%m.%Y %H:%M") if dup.started_at else "?"
            if not ask_yes_no(
                self, "Файл уже загружали",
                f"Этот файл уже импортировали {when} («{dup.source_file_name}»).\n"
                f"Загрузить повторно?",
                default_yes=False,
            ):
                return

        try:
            # Сначала проверка: пустой или чужой отчёт стал бы «последним» и
            # опустошил бы «Отгрузку» и «Залежавшиеся».
            rows = self.import_service.parser.parse_file(file_path)
            errors, warnings = ExcelParser.check_report(rows)
            if errors:
                QMessageBox.critical(
                    self, "Файл не подходит",
                    "\n\n".join(errors) + "\n\nФайл не загружен, база не изменилась.",
                )
                return
            if warnings and not ask_yes_no(
                self, "Проверьте отчёт",
                "\n\n".join(warnings) + "\n\nЗагрузить файл?",
                default_yes=False,
            ):
                return

            session = self.import_service.process_import(file_path, rows)
            self.last_import_session_id = session.id

            self.file_name_label.setText(session.source_file_name)
            self.tile_total.setText(str(session.total_rows))
            self.tile_matched.setText(str(session.matched_rows))
            self.tile_new.setText(str(session.new_to_ship_rows))
            self.tile_on_point.setText(str(session.already_on_point))
            self.tile_returned.setText(str(session.returned_rows))
            self.tile_not_ours.setText(str(session.not_ours_rows))
            self.tile_kty.setText(str(session.kty_rows))

            self.stats_card.setVisible(True)
            self.export_card.setVisible(True)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка импорта",
                                 f"Не удалось импортировать файл:\n{e}")
            return

        summary = (
            f"Распознано наших посылок: {session.matched_rows}.\n"
            f"Новых к отгрузке: {session.new_to_ship_rows}."
        )
        alert_text = self.refresh_return_alert()
        if alert_text:
            QMessageBox.warning(self, "Импорт завершён",
                                summary + "\n\nСкоро возврат. " + alert_text)
        else:
            QMessageBox.information(self, "Импорт завершён", summary)
        # Последним действием: синхронизация может заменить базу и пересоздать
        # этот экран.
        self.main_window.on_data_changed()

    def on_export(self, point):
        if not self.last_import_session_id:
            QMessageBox.information(self, "Нет импорта",
                                    "Сначала импортируйте файл от Казакова.")
            return

        default_name = (
            f"Отгрузка_"
            f"{'Комсомольская' if point == DeliveryPoint.KOMSOMOLSKAYA_4 else 'Кольцевая'}_"
            f"{datetime.now().strftime('%d.%m.%Y')}.xlsx"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить отгрузку", default_name, "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        try:
            self.export_service.generate_export(
                point, file_path, self.last_import_session_id
            )
            QMessageBox.information(self, "Готово", f"Файл сохранён:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта",
                                 f"Не удалось сохранить файл:\n{e}")
            return
        # Выгрузка помечает посылки как выгруженные — это тоже изменение базы.
        self.main_window.on_data_changed()

    def release(self):
        """Закрыть долгоживущие сессии: файл базы сейчас будут читать или заменять."""
        self.import_service.session.close()
        self.export_service.session.close()
