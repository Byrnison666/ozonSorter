import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from .models import Base

# Default DB path in AppData if not specified
APP_DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'OzonSorter')
DEFAULT_DB_PATH = os.path.join(APP_DATA_DIR, 'ozon_sorter.db')

class DatabaseManager:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)
            
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def create_tables(self):
        Base.metadata.create_all(bind=self.engine)
        self._migrate()

    def _migrate(self):
        # Идемпотентные миграции для БД, созданных ранними версиями. create_all не
        # делает ALTER существующих таблиц, поэтому новые колонки добавляем вручную.
        with self.engine.begin() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(shipments)"))]
            if "last_seen_import_session_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE shipments "
                    "ADD COLUMN last_seen_import_session_id INTEGER"
                ))
                # Бэкфилл: для старых строк лучший доступный ориентир — сессия
                # первой встречи. Со следующего импорта значение станет точным.
                conn.execute(text(
                    "UPDATE shipments "
                    "SET last_seen_import_session_id = import_session_id "
                    "WHERE last_seen_import_session_id IS NULL"
                ))

    def get_session(self) -> Session:
        return self.SessionLocal()
