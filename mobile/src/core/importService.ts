/**
 * Импорт отчёта склада в базу — перенос ImportService (src/services.py).
 * Результат в базе должен совпадать с ПК построчно: проверяет tests/import.test.ts.
 */
import type { Db, SqlValue } from './db';
import { type CellValue, PyDateTime, PyTime, pyJsonDumpsStrings, pyStr, pyStrip } from './py';
import { isReadyForPickup, normalizeOzonId, type ParsedRow } from './parser';

export const AssignmentStatus = {
  TO_ASSIGN: 'TO_ASSIGN',
  TO_SHIP: 'TO_SHIP',
  ON_POINT: 'ON_POINT',
  DELIVERED: 'DELIVERED',
  RETURNED: 'RETURNED',
  EXCLUDED_NOT_OURS: 'EXCLUDED_NOT_OURS',
  EXCLUDED_KTY: 'EXCLUDED_KTY',
} as const;
export type AssignmentStatus = (typeof AssignmentStatus)[keyof typeof AssignmentStatus];

export class ImportError extends Error {}

export interface ImportSessionRow {
  id: number;
  source_file_name: string;
  source_file_sha256: string;
  started_at: string;
  finished_at: string | null;
  total_rows: number;
  kty_rows: number;
  matched_rows: number;
  new_to_ship_rows: number;
  already_on_point: number;
  returned_rows: number;
  not_ours_rows: number;
  errors_rows: number;
  log_json: string | null;
}

interface ShipmentState {
  id: number;
  assignment_status: AssignmentStatus;
}

interface ClientRef {
  id: number;
  fixed_delivery_point: string | null;
}

/** Значение ячейки так, как его записал бы sqlite3 Python в колонку-строку. */
function bindCell(value: CellValue | undefined): SqlValue {
  if (value === null || value === undefined) return null;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (value instanceof PyDateTime || value instanceof PyTime) return value.toString();
  return value;
}

/** Колонка DateTime: SQLAlchemy принимает только datetime/None, иначе импорт падает. */
function bindDateTime(value: CellValue | undefined, postingNumber: CellValue): SqlValue {
  if (value === null || value === undefined) return null;
  if (value instanceof PyDateTime) return value.toSql();
  throw new ImportError(
    `Не удалось сохранить дату отправки «${pyStr(value)}» у посылки ${pyStr(postingNumber)}: ` +
      'ожидается дата Excel. Импорт отменён.',
  );
}

/** Ключ строки в пределах файла — как кортеж (posting_number, product_label) в Python. */
function fileKey(row: ParsedRow): string {
  const p = row.posting_number;
  const kind = typeof p === 'number' || typeof p === 'boolean' ? 'n' : 's';
  const value = typeof p === 'boolean' ? Number(p) : pyStr(p);
  return JSON.stringify([kind, value, row.product_label]);
}

export function findDuplicateImport(db: Db, sha256: string): ImportSessionRow | undefined {
  return db.get<ImportSessionRow>(
    'SELECT * FROM import_sessions WHERE source_file_sha256 = ?', [sha256],
  );
}

export interface ImportClock {
  now(): PyDateTime;
}

const systemClock: ImportClock = { now: () => PyDateTime.now() };

/**
 * process_import. fileName — имя файла без пути, sha256 — хэш его содержимого.
 * Всё в одной транзакции: при ошибке база не меняется.
 */
export function processImport(
  db: Db, fileName: string, sha256: string, rows: ParsedRow[], clock: ImportClock = systemClock,
): ImportSessionRow {
  let sessionId = 0;
  db.transaction(() => {
    sessionId = db.run(
      'INSERT INTO import_sessions (source_file_name, source_file_sha256, started_at, ' +
        'finished_at, total_rows, kty_rows, matched_rows, new_to_ship_rows, already_on_point, ' +
        'returned_rows, not_ours_rows, errors_rows, log_json) ' +
        'VALUES (?, ?, ?, NULL, 0, 0, 0, 0, 0, 0, 0, 0, NULL)',
      [fileName, sha256, clock.now().toSql()],
    ).lastInsertRowId;

    const counters = {
      kty_rows: 0, matched_rows: 0, new_to_ship_rows: 0, already_on_point: 0,
      returned_rows: 0, not_ours_rows: 0,
    };
    const logs: string[] = [];

    // Активные клиенты по нормализованному Ozon ID; при совпадении — первый.
    const clientsByNorm = new Map<string, ClientRef>();
    for (const c of db.all<ClientRef & { ozon_client_id: string }>(
      'SELECT id, ozon_client_id, fixed_delivery_point FROM clients WHERE is_active = 1 ORDER BY id',
    )) {
      const norm = normalizeOzonId(c.ozon_client_id);
      if (!clientsByNorm.has(norm)) clientsByNorm.set(norm, c);
    }

    const touch = (shipmentId: number, row: ParsedRow) => {
      db.run(
        'UPDATE shipments SET last_seen_at = ?, last_seen_import_session_id = ? WHERE id = ?',
        [clock.now().toSql(), sessionId, shipmentId],
      );
      // Пустая (в т.ч. из пробелов) ячейка не затирает известную.
      const cell = row.cell;
      if (cell !== null && cell !== undefined && pyStrip(pyStr(cell))) {
        db.run('UPDATE shipments SET cell = ? WHERE id = ?', [bindCell(cell), shipmentId]);
      }
    };

    const create = (
      row: ParsedRow, status: AssignmentStatus, clientId: number | null = null,
      assignedPoint: string | null = null,
    ) => {
      const firstSeen = clock.now().toSql();
      const lastSeen = clock.now().toSql();
      db.run(
        'INSERT INTO shipments (posting_number, client_id, ozon_client_id_raw, product_label, ' +
          'product_name, ozon_type, ozon_status, cell, shipment_date_ozon, is_damaged, is_kty, ' +
          'barcode, assignment_status, assigned_point, import_session_id, ' +
          'last_seen_import_session_id, exported_import_session_id, first_seen_at, last_seen_at, ' +
          'shipped_to_point_at, delivered_at, notes) ' +
          'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL)',
        [
          bindCell(row.posting_number), clientId, row.ozon_client_id ?? '',
          row.product_label, row.product_name, bindCell(row.type), bindCell(row.status),
          bindCell(row.cell), bindDateTime(row.shipment_date_ozon, row.posting_number),
          row.is_damaged ? 1 : 0, row.is_kty ? 1 : 0, status, assignedPoint, sessionId, sessionId,
          firstSeen, lastSeen,
        ],
      );
    };

    const seenInFile = new Set<string>();
    for (const row of rows) {
      const key = fileKey(row);
      if (seenInFile.has(key)) continue;
      seenInFile.add(key);

      const existing = db.get<ShipmentState>(
        'SELECT id, assignment_status FROM shipments WHERE posting_number = ? AND product_label = ?',
        [bindCell(row.posting_number), row.product_label],
      );

      if (row.is_kty) {
        counters.kty_rows++;
        if (existing) touch(existing.id, row);
        else create(row, AssignmentStatus.EXCLUDED_KTY);
        continue;
      }

      const client = clientsByNorm.get(normalizeOzonId(row.ozon_client_id));
      if (!client) {
        counters.not_ours_rows++;
        if (existing) touch(existing.id, row);
        else create(row, AssignmentStatus.EXCLUDED_NOT_OURS);
        continue;
      }

      counters.matched_rows++;
      const isReady = isReadyForPickup(row.status, row.cell);
      const status = bindCell(row.status);

      if (existing) {
        touch(existing.id, row);
        const current = existing.assignment_status;
        if (current === AssignmentStatus.EXCLUDED_NOT_OURS) {
          // Клиента добавили после первой встречи посылки — переклассифицировать.
          if (isReady) {
            db.run(
              'UPDATE shipments SET client_id = ?, assigned_point = ?, ozon_status = ?, ' +
                'assignment_status = ?, exported_import_session_id = NULL WHERE id = ?',
              [client.id, client.fixed_delivery_point, status, AssignmentStatus.TO_SHIP, existing.id],
            );
            counters.new_to_ship_rows++;
          } else {
            db.run(
              'UPDATE shipments SET client_id = ?, assigned_point = ?, ozon_status = ?, ' +
                'assignment_status = ? WHERE id = ?',
              [client.id, client.fixed_delivery_point, status, AssignmentStatus.RETURNED, existing.id],
            );
            counters.returned_rows++;
          }
        } else if (!isReady) {
          // Возврат снимает с отгрузки; привезённые и выданные не трогаем.
          if (current === AssignmentStatus.TO_SHIP || current === AssignmentStatus.TO_ASSIGN) {
            db.run('UPDATE shipments SET assignment_status = ? WHERE id = ?',
              [AssignmentStatus.RETURNED, existing.id]);
          }
          db.run('UPDATE shipments SET ozon_status = ? WHERE id = ?', [status, existing.id]);
          counters.returned_rows++;
        } else if (current === AssignmentStatus.ON_POINT) {
          counters.already_on_point++;
          logs.push(`Shipment ${pyStr(row.posting_number)} already on point.`);
        } else if (current === AssignmentStatus.DELIVERED) {
          logs.push(
            `WARNING: Shipment ${pyStr(row.posting_number)} marked as DELIVERED but seen again in import.`,
          );
        } else if (current === AssignmentStatus.RETURNED) {
          // Снова «Готово к выдаче» — обратно в отгрузку, отметку выгрузки сбросить.
          db.run(
            'UPDATE shipments SET assignment_status = ?, assigned_point = ?, ozon_status = ?, ' +
              'exported_import_session_id = NULL WHERE id = ?',
            [AssignmentStatus.TO_SHIP, client.fixed_delivery_point, status, existing.id],
          );
          counters.new_to_ship_rows++;
        } else {
          // Ещё не привезена: точка — актуальная у клиента.
          db.run('UPDATE shipments SET assigned_point = ? WHERE id = ?',
            [client.fixed_delivery_point, existing.id]);
        }
      } else if (isReady) {
        create(row, AssignmentStatus.TO_SHIP, client.id, client.fixed_delivery_point);
        counters.new_to_ship_rows++;
      } else {
        create(row, AssignmentStatus.RETURNED, client.id, client.fixed_delivery_point);
        counters.returned_rows++;
      }
    }

    db.run(
      'UPDATE import_sessions SET total_rows = ?, kty_rows = ?, matched_rows = ?, ' +
        'new_to_ship_rows = ?, already_on_point = ?, returned_rows = ?, not_ours_rows = ?, ' +
        'errors_rows = 0, finished_at = ?, log_json = ? WHERE id = ?',
      [
        rows.length, counters.kty_rows, counters.matched_rows, counters.new_to_ship_rows,
        counters.already_on_point, counters.returned_rows, counters.not_ours_rows,
        clock.now().toSql(), pyJsonDumpsStrings(logs), sessionId,
      ],
    );
  });
  return db.get<ImportSessionRow>('SELECT * FROM import_sessions WHERE id = ?', [sessionId])!;
}
