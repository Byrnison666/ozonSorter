import sys

from PySide2.QtGui import QFont
from PySide2.QtWidgets import QApplication

from src.backup import backup_database
from src.database import DatabaseManager
from src.ui.main_window import MainWindow
from src.ui.theme import QSS


def main():
    try:
        backup_database()
    except Exception as e:
        print(f"Failed to backup: {e}")

    db_manager = DatabaseManager()
    db_manager.create_tables()

    app = QApplication(sys.argv)
    app.setApplicationName("OzonSorter")
    app.setStyle("Fusion")

    default_font = QFont("Segoe UI Variable Text", 10)
    if not default_font.exactMatch():
        default_font = QFont("Segoe UI", 10)
    app.setFont(default_font)

    app.setStyleSheet(QSS)

    window = MainWindow(db_manager)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
