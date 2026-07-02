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
            if "exported_import_session_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE shipments "
                    "ADD COLUMN exported_import_session_id INTEGER"
                ))
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
            if "barcode" not in cols:
                # Колонка есть в модели с начала, но старейшие базы могли её не
                # иметь; нужна до rebuild ниже (перечисляем её в INSERT SELECT).
                conn.execute(text("ALTER TABLE shipments ADD COLUMN barcode VARCHAR"))

            # Смена ключа идентификации посылки: было UNIQUE(posting_number), стало
            # UNIQUE(posting_number, product_label). Один и тот же номер отправления
            # Ozon может прийти для физически другого товара (переиспользована
            # ячейка, другой штрихкод того же клиента) — старая схема схлопывала их
            # в дубль. SQLite не меняет UNIQUE через ALTER, поэтому перестраиваем
            # таблицу. Детект: есть ли unique-индекс ровно по одной колонке
            # posting_number (после rebuild такого не останется → идемпотентно).
            needs_rebuild = False
            for idx in conn.execute(text("PRAGMA index_list(shipments)")):
                idx_name, is_unique = idx[1], idx[2]
                if not is_unique:
                    continue
                idx_cols = [r[2] for r in conn.execute(
                    text(f"PRAGMA index_info('{idx_name}')")
                )]
                if idx_cols == ["posting_number"]:
                    needs_rebuild = True
                    break

            if needs_rebuild:
                # Пустой/NULL штрихкод → номер отправления: NULL в UNIQUE SQLite
                # считает различным, дубли не поймались бы. В боевых базах этикетка
                # почти всегда заполнена (первая строка «Этикетка»).
                conn.execute(text(
                    "UPDATE shipments SET product_label = posting_number "
                    "WHERE product_label IS NULL OR product_label = ''"
                ))
                # Колонки перечисляем по именам (ALTER ADD COLUMN дописывает в конец,
                # физический порядок мог разойтись с моделью). FK-проверка SQLite по
                # умолчанию выключена → DROP/RENAME безопасны, id сохраняются,
                # ссылки clients.id/import_sessions.id остаются валидными.
                ship_cols = (
                    "id, posting_number, client_id, ozon_client_id_raw, product_label,"
                    " product_name, ozon_type, ozon_status, cell, shipment_date_ozon,"
                    " is_damaged, is_kty, barcode, assignment_status, assigned_point,"
                    " import_session_id, last_seen_import_session_id,"
                    " exported_import_session_id, first_seen_at, last_seen_at,"
                    " shipped_to_point_at, delivered_at, notes"
                )
                conn.execute(text(
                    "CREATE TABLE shipments_new ("
                    " id INTEGER NOT NULL PRIMARY KEY,"
                    " posting_number VARCHAR NOT NULL,"
                    " client_id INTEGER,"
                    " ozon_client_id_raw VARCHAR NOT NULL,"
                    " product_label VARCHAR,"
                    " product_name VARCHAR,"
                    " ozon_type VARCHAR,"
                    " ozon_status VARCHAR,"
                    " cell VARCHAR,"
                    " shipment_date_ozon DATETIME,"
                    " is_damaged BOOLEAN,"
                    " is_kty BOOLEAN,"
                    " barcode VARCHAR,"
                    " assignment_status VARCHAR NOT NULL,"
                    " assigned_point VARCHAR,"
                    " import_session_id INTEGER,"
                    " last_seen_import_session_id INTEGER,"
                    " exported_import_session_id INTEGER,"
                    " first_seen_at DATETIME,"
                    " last_seen_at DATETIME,"
                    " shipped_to_point_at DATETIME,"
                    " delivered_at DATETIME,"
                    " notes TEXT,"
                    " UNIQUE (posting_number, product_label)"
                    ")"
                ))
                conn.execute(text(
                    f"INSERT INTO shipments_new ({ship_cols}) "
                    f"SELECT {ship_cols} FROM shipments"
                ))
                conn.execute(text("DROP TABLE shipments"))
                conn.execute(text("ALTER TABLE shipments_new RENAME TO shipments"))
                conn.execute(text(
                    "CREATE INDEX idx_shipments_assignment_status"
                    " ON shipments (assignment_status)"
                ))
                conn.execute(text(
                    "CREATE INDEX idx_shipments_assigned_point"
                    " ON shipments (assigned_point)"
                ))
                conn.execute(text(
                    "CREATE INDEX idx_shipments_ozon_client_id_raw"
                    " ON shipments (ozon_client_id_raw)"
                ))
                conn.execute(text(
                    "CREATE INDEX idx_shipments_last_seen_import_session_id"
                    " ON shipments (last_seen_import_session_id)"
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
