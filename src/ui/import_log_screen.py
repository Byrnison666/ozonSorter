from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame,
)
from sqlalchemy import select

from src.models import ImportSession


_COLUMNS = [
    "Дата", "Файл", "Всего", "Наших", "Новых к отгрузке",
    "Возвраты", "Уже на точках", "Не наши", "КТЯ", "Примечание",
]


class ImportLogScreen(QWidget):
    """Журнал всех импортов отчётов Казакова — чтобы видеть, что ни один отчёт
    не пропущен и не загружен дважды. Список по убыванию даты, со статистикой
    каждой загрузки и пометкой повторно загруженного файла (тот же sha256)."""

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
        h1 = QLabel("Журнал импортов")
        h1.setObjectName("h1")
        title_block.addWidget(h1)
        sub = QLabel("Все загрузки отчётов Казакова со статистикой — чтобы ничего не пропустить.")
        sub.setObjectName("subtitle")
        title_block.addWidget(sub)
        header.addLayout(title_block, 1)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.refresh_table)
        header.addWidget(self.refresh_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(2, 2, 2, 2)
        card_lay.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.Stretch)  # «Файл» тянется
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(38)
        card_lay.addWidget(self.table)
        layout.addWidget(card, 1)

        self.empty_label = QLabel("Импортов ещё не было.")
        self.empty_label.setObjectName("muted")
        layout.addWidget(self.empty_label)

        self.refresh_table()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_table()

    def refresh_table(self):
        session = self.db_manager.get_session()
        try:
            sessions = session.execute(
                select(ImportSession).order_by(ImportSession.started_at.desc())
            ).scalars().all()
        finally:
            session.close()

        # Повтор файла: тот же sha256 уже грузили в более раннюю сессию.
        seen_sha = set()
        repeat_ids = set()
        for s in sorted(sessions, key=lambda x: (x.started_at or x.id)):
            if s.source_file_sha256 in seen_sha:
                repeat_ids.add(s.id)
            seen_sha.add(s.source_file_sha256)

        self.empty_label.setVisible(not sessions)
        self.table.setRowCount(0)
        for s in sessions:
            row = self.table.rowCount()
            self.table.insertRow(row)
            note = "повтор файла" if s.id in repeat_ids else "—"
            values = [
                s.started_at.strftime("%d.%m.%Y %H:%M") if s.started_at else "—",
                s.source_file_name or "—",
                s.total_rows, s.matched_rows, s.new_to_ship_rows,
                s.returned_rows, s.already_on_point, s.not_ours_rows, s.kty_rows,
                note,
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                if col >= 2 and col <= 8:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)
