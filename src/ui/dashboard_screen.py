from datetime import datetime

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QMessageBox, QFrame, QGridLayout
)

from src.export_service import ExportService
from src.models import DeliveryPoint
from src.services import ImportService

from .theme import make_stat_tile


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

        # --- Drop zone card ---
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setMinimumHeight(180)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setContentsMargins(20, 28, 20, 28)
        drop_layout.setSpacing(10)
        drop_layout.addStretch()

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

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignCenter)
        self.import_btn = QPushButton("Выбрать файл…")
        self.import_btn.setObjectName("primary")
        self.import_btn.clicked.connect(self.on_import)
        btn_row.addWidget(self.import_btn)
        drop_layout.addLayout(btn_row)
        drop_layout.addStretch()

        layout.addWidget(self.drop_zone)

        # --- Stats grid (hidden until import) ---
        self.stats_card = QFrame()
        self.stats_card.setObjectName("card")
        stats_layout = QVBoxLayout(self.stats_card)
        stats_layout.setContentsMargins(20, 18, 20, 18)
        stats_layout.setSpacing(14)

        stats_title = QLabel("Результаты последнего импорта")
        stats_title.setObjectName("h2")
        stats_layout.addWidget(stats_title)

        self.file_name_label = QLabel("—")
        self.file_name_label.setObjectName("muted")
        stats_layout.addWidget(self.file_name_label)

        tiles_layout = QHBoxLayout()
        tiles_layout.setSpacing(12)
        _, self.tile_total = make_stat_tile(tiles_layout, "Всего строк", "0")
        _, self.tile_matched = make_stat_tile(tiles_layout, "Наших", "0")
        _, self.tile_new = make_stat_tile(tiles_layout, "Новых к отгрузке", "0")
        _, self.tile_on_point = make_stat_tile(tiles_layout, "Уже на точках", "0")
        _, self.tile_not_ours = make_stat_tile(tiles_layout, "Не наши", "0")
        _, self.tile_kty = make_stat_tile(tiles_layout, "КТЯ", "0")
        stats_layout.addLayout(tiles_layout)

        layout.addWidget(self.stats_card)
        self.stats_card.setVisible(False)

        # --- Export card ---
        self.export_card = QFrame()
        self.export_card.setObjectName("card")
        export_layout = QVBoxLayout(self.export_card)
        export_layout.setContentsMargins(20, 18, 20, 18)
        export_layout.setSpacing(12)

        exp_title = QLabel("Формирование отгрузки")
        exp_title.setObjectName("h2")
        export_layout.addWidget(exp_title)

        exp_sub = QLabel(
            "Два независимых Excel — отдельно для каждой точки. "
            "Папка сохранения запоминается."
        )
        exp_sub.setObjectName("subtitle")
        export_layout.addWidget(exp_sub)

        buttons_grid = QGridLayout()
        buttons_grid.setSpacing(12)
        buttons_grid.setColumnStretch(0, 1)
        buttons_grid.setColumnStretch(1, 1)

        self.export_koms_btn = QPushButton("Excel для Комсомольской 4")
        self.export_koms_btn.setObjectName("primary")
        self.export_koms_btn.setMinimumHeight(44)
        self.export_koms_btn.clicked.connect(
            lambda: self.on_export(DeliveryPoint.KOMSOMOLSKAYA_4)
        )
        buttons_grid.addWidget(self.export_koms_btn, 0, 0)

        self.export_kolt_btn = QPushButton("Excel для Кольцевой 16")
        self.export_kolt_btn.setObjectName("primary")
        self.export_kolt_btn.setMinimumHeight(44)
        self.export_kolt_btn.clicked.connect(
            lambda: self.on_export(DeliveryPoint.KOLTSEVAYA_16)
        )
        buttons_grid.addWidget(self.export_kolt_btn, 0, 1)

        export_layout.addLayout(buttons_grid)

        layout.addWidget(self.export_card)
        self.export_card.setVisible(False)

        layout.addStretch()

    def on_import(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите отчёт Ozon", "", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        try:
            session = self.import_service.process_import(file_path)
            self.last_import_session_id = session.id

            self.file_name_label.setText(session.source_file_name)
            self.tile_total.setText(str(session.total_rows))
            self.tile_matched.setText(str(session.matched_rows))
            self.tile_new.setText(str(session.new_to_ship_rows))
            self.tile_on_point.setText(str(session.already_on_point))
            self.tile_not_ours.setText(str(session.not_ours_rows))
            self.tile_kty.setText(str(session.kty_rows))

            self.stats_card.setVisible(True)
            self.export_card.setVisible(True)

            QMessageBox.information(
                self, "Импорт завершён",
                f"Распознано наших посылок: {session.matched_rows}.\n"
                f"Новых к отгрузке: {session.new_to_ship_rows}."
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка импорта",
                                 f"Не удалось импортировать файл:\n{e}")

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
