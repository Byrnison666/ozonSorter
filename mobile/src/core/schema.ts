import type { Db } from './db';

/**
 * Версия схемы — PRAGMA user_version, как SCHEMA_VERSION в src/database.py на ПК.
 * Поднимать синхронно с ПК.
 */
export const SCHEMA_VERSION = 1;

/**
 * DDL свежей базы ПК (SQLAlchemy create_all + _migrate), дословно. Совпадение
 * проверяет tests/schema.test.ts. Базы, пришедшие с ПК, могут иметь другой текст
 * схемы после старых миграций, поэтому код обращается к колонкам только по именам
 * и схему чужой базы не меняет.
 */
const DDL: readonly string[] = [
  'CREATE TABLE clients (\n\tid INTEGER NOT NULL, \n\tozon_client_id VARCHAR NOT NULL, \n\tfull_name VARCHAR, \n\tphone VARCHAR, \n\tfixed_delivery_point VARCHAR(15), \n\tnotes TEXT, \n\tis_active BOOLEAN NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (ozon_client_id)\n)',
  'CREATE INDEX idx_clients_is_active ON clients (is_active)',
  'CREATE TABLE import_sessions (\n\tid INTEGER NOT NULL, \n\tsource_file_name VARCHAR NOT NULL, \n\tsource_file_sha256 VARCHAR NOT NULL, \n\tstarted_at DATETIME NOT NULL, \n\tfinished_at DATETIME, \n\ttotal_rows INTEGER NOT NULL, \n\tkty_rows INTEGER NOT NULL, \n\tmatched_rows INTEGER NOT NULL, \n\tnew_to_ship_rows INTEGER NOT NULL, \n\talready_on_point INTEGER NOT NULL, \n\treturned_rows INTEGER NOT NULL, \n\tnot_ours_rows INTEGER NOT NULL, \n\terrors_rows INTEGER NOT NULL, \n\tlog_json TEXT, \n\tPRIMARY KEY (id)\n)',
  'CREATE TABLE shipments (\n\tid INTEGER NOT NULL, \n\tposting_number VARCHAR NOT NULL, \n\tclient_id INTEGER, \n\tozon_client_id_raw VARCHAR NOT NULL, \n\tproduct_label VARCHAR, \n\tproduct_name VARCHAR, \n\tozon_type VARCHAR, \n\tozon_status VARCHAR, \n\tcell VARCHAR, \n\tshipment_date_ozon DATETIME, \n\tis_damaged BOOLEAN NOT NULL, \n\tis_kty BOOLEAN NOT NULL, \n\tbarcode VARCHAR, \n\tassignment_status VARCHAR(17) NOT NULL, \n\tassigned_point VARCHAR(15), \n\timport_session_id INTEGER NOT NULL, \n\tlast_seen_import_session_id INTEGER, \n\texported_import_session_id INTEGER, \n\tfirst_seen_at DATETIME NOT NULL, \n\tlast_seen_at DATETIME NOT NULL, \n\tshipped_to_point_at DATETIME, \n\tdelivered_at DATETIME, \n\tnotes TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_posting_label UNIQUE (posting_number, product_label), \n\tFOREIGN KEY(client_id) REFERENCES clients (id), \n\tFOREIGN KEY(import_session_id) REFERENCES import_sessions (id), \n\tFOREIGN KEY(last_seen_import_session_id) REFERENCES import_sessions (id), \n\tFOREIGN KEY(exported_import_session_id) REFERENCES import_sessions (id)\n)',
  'CREATE INDEX idx_shipments_assigned_point ON shipments (assigned_point)',
  'CREATE INDEX idx_shipments_last_seen_import_session_id ON shipments (last_seen_import_session_id)',
  'CREATE INDEX idx_shipments_ozon_client_id_raw ON shipments (ozon_client_id_raw)',
  'CREATE INDEX idx_shipments_assignment_status ON shipments (assignment_status)',
  'CREATE TABLE export_sessions (\n\tid INTEGER NOT NULL, \n\timport_session_id INTEGER NOT NULL, \n\tdelivery_point VARCHAR(15) NOT NULL, \n\texport_date DATETIME NOT NULL, \n\tfile_path VARCHAR NOT NULL, \n\tshipments_count INTEGER NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(import_session_id) REFERENCES import_sessions (id)\n)',
];

export class SchemaError extends Error {}

/** Настройки соединения — как на ПК: журнал DELETE (файл базы всегда цельный
 * без -wal), схема базы с Диска не вызывает ничего, кроме безобидных функций. */
export function configureConnection(db: Db): void {
  db.exec('PRAGMA journal_mode = DELETE; PRAGMA trusted_schema = OFF;');
}

/**
 * Пустую базу — создать со схемой ПК; существующую — только проверить версию.
 * Схему существующей базы не трогаем: любая запись меняла бы файл, и
 * синхронизация считала бы это локальной правкой.
 */
export function ensureSchema(db: Db): void {
  const tables = db.get<{ n: number }>(
    "SELECT count(*) AS n FROM sqlite_master WHERE type = 'table'",
  )!.n;
  if (tables === 0) {
    db.transaction(() => {
      for (const sql of DDL) db.exec(sql);
      db.exec(`PRAGMA user_version = ${SCHEMA_VERSION}`);
    });
    return;
  }
  const version = db.get<{ user_version: number }>('PRAGMA user_version')!.user_version;
  if (version > SCHEMA_VERSION) {
    throw new SchemaError(
      'База создана более новой версией программы. Обновите приложение на этом устройстве.',
    );
  }
}
