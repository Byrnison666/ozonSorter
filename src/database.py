import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from .models import Base
from .parser import ExcelParser

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
            icols = [r[1] for r in conn.execute(text("PRAGMA table_info(import_sessions)"))]
            if "returned_rows" not in icols:
                conn.execute(text(
                    "ALTER TABLE import_sessions ADD COLUMN returned_rows INTEGER DEFAULT 0"
                ))

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

            # Удаление колонки delivery_point_policy из clients. DROP COLUMN не
            # проходит из-за CHECK-констрейнта на неё, поэтому перестраиваем таблицу.
            # FK-проверка SQLite по умолчанию выключена → DROP/RENAME безопасны,
            # id сохраняются, ссылки shipments.client_id остаются валидными.
            ccols = [r[1] for r in conn.execute(text("PRAGMA table_info(clients)"))]
            if "delivery_point_policy" in ccols:
                conn.execute(text(
                    "CREATE TABLE clients_new ("
                    " id INTEGER NOT NULL PRIMARY KEY,"
                    " ozon_client_id VARCHAR NOT NULL UNIQUE,"
                    " full_name VARCHAR,"
                    " phone VARCHAR,"
                    " fixed_delivery_point VARCHAR,"
                    " notes TEXT,"
                    " is_active BOOLEAN,"
                    " created_at DATETIME,"
                    " updated_at DATETIME"
                    ")"
                ))
                conn.execute(text(
                    "INSERT INTO clients_new"
                    " (id, ozon_client_id, full_name, phone, fixed_delivery_point,"
                    "  notes, is_active, created_at, updated_at)"
                    " SELECT id, ozon_client_id, full_name, phone, fixed_delivery_point,"
                    "  notes, is_active, created_at, updated_at FROM clients"
                ))
                conn.execute(text("DROP TABLE clients"))
                conn.execute(text("ALTER TABLE clients_new RENAME TO clients"))
                conn.execute(text(
                    "CREATE INDEX idx_clients_is_active ON clients (is_active)"
                ))

            # Дедуп клиентов-дублей по нормализованному Ozon ID (lstrip нулей).
            # Базы до v1.6 могли накопить «0224933356» и «224933356» как два
            # клиента на одного: матчинг (он нормализует) молча отдавал посылку
            # лишь одному, а импорт v1.6 при канонизации id падал UNIQUE. Сводим
            # в одного: посылки перецепляем на выжившего, лишних удаляем, id
            # канонизируем. Выживший — наименьший id (старейшая запись).
            # Идемпотентно: после прогона на каждый норм-id ровно одна запись.
            client_rows = list(conn.execute(text(
                "SELECT id, ozon_client_id FROM clients"
            )))
            groups: dict = {}
            for cid, oid in client_rows:
                groups.setdefault(ExcelParser.normalize_ozon_id(oid), []).append((cid, oid))
            for norm, members in groups.items():
                members.sort(key=lambda m: m[0])
                survivor_id, survivor_oid = members[0]
                for loser_id, _ in members[1:]:
                    conn.execute(
                        text("UPDATE shipments SET client_id = :s WHERE client_id = :l"),
                        {"s": survivor_id, "l": loser_id},
                    )
                    conn.execute(
                        text("DELETE FROM clients WHERE id = :l"),
                        {"l": loser_id},
                    )
                # Канонизируем хранимый id выжившего (после удаления дублей —
                # коллизии UNIQUE уже нет).
                if survivor_oid != norm:
                    conn.execute(
                        text("UPDATE clients SET ozon_client_id = :n WHERE id = :i"),
                        {"n": norm, "i": survivor_id},
                    )

    def get_session(self) -> Session:
        return self.SessionLocal()
