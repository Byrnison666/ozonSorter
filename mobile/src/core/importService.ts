/**
 * Импорт отчёта склада в базу — перенос ImportService (src/services.py).
 * Результат в базе должен совпадать с ПК построчно: проверяет tests/import.test.ts.
 */
import type { Db, SqlValue } from './db';
import { DUPLICATE_DAYS, isIncomplete, isIssued } from './duplicates';
import { type CellValue, PyDateTime, PyTime, pyJsonDumpsStrings, pyStr, pyStrip } from './py';
import { AssignmentStatus, type Clock, systemClock } from './models';
import { isReadyForPickup, normalizeOzonId, type ParsedRow } from './parser';
import { SCHEMA_VERSION } from './schema';
import { daysBetween } from './staleService';

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

export interface ImportResult {
  session: ImportSessionRow;
  /** Выданных посылок, которые снова пришли после неполного прошлого отчёта и не стали дублями. */
  skippedDuplicates: number;
}

interface ShipmentState {
  id: number;
  assignment_status: AssignmentStatus;
  exported_import_session_id: number | null;
  last_seen_import_session_id: number | null;
  last_seen_at: string;
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

/** (id прошлого импорта, id позапрошлого); 0 и null — таких нет. */
function previousSessions(db: Db, sessionId: number): [number, number | null] {
  const ids = db.all<{ id: number }>(
    'SELECT id FROM import_sessions WHERE id < ? ORDER BY id DESC LIMIT 2', [sessionId],
  ).map((r) => r.id);
  return [ids[0] ?? 0, ids[1] ?? null];
}

/**
 * [lost, kept] по уникальным посылкам отчёта, включая чужие и КТЯ: lost — были
 * в позапрошлом отчёте, но не в прошлом; kept — были в прошлом. Много lost —
 * прошлый отчёт пришёл неполным.
 */
function missingInPrevious(
  db: Db, rows: ParsedRow[], previousId: number, beforePreviousId: number | null,
): [number, number] {
  if (beforePreviousId === null) return [0, 0];
  const lastSeen = new Map<string, number>();
  for (const s of db.all<{ posting_number: string; product_label: string | null; last_seen_import_session_id: number }>(
    'SELECT posting_number, product_label, last_seen_import_session_id FROM shipments ' +
      'WHERE last_seen_import_session_id IN (?, ?)',
    [previousId, beforePreviousId],
  )) {
    lastSeen.set(JSON.stringify([s.posting_number, s.product_label]), s.last_seen_import_session_id);
  }
  let lost = 0;
  let kept = 0;
  const keys = new Set<string>();
  for (const row of rows) {
    // В Python ключ — кортеж из значения ячейки: номер-число не равен строке из базы.
    if (typeof row.posting_number !== 'string') continue;
    const key = JSON.stringify([row.posting_number, row.product_label]);
    if (keys.has(key)) continue;
    keys.add(key);
    const seen = lastSeen.get(key);
    if (seen === beforePreviousId) lost++;
    else if (seen === previousId) kept++;
  }
  return [lost, kept];
}

export function findDuplicateImport(db: Db, sha256: string): ImportSessionRow | undefined {
  return db.get<ImportSessionRow>(
    'SELECT * FROM import_sessions WHERE source_file_sha256 = ?', [sha256],
  );
}

/**
 * process_import. fileName — имя файла без пути, sha256 — хэш его содержимого.
 * Всё в одной транзакции: при ошибке база не меняется.
 */
export function processImport(
  db: Db, fileName: string, sha256: string, rows: ParsedRow[], clock: Clock = systemClock,
): ImportResult {
  let sessionId = 0;
  let skippedDuplicates = 0;
  db.transaction(() => {
    // Импорт может записать статус DUPLICATE, которого нет у программ схемы 1:
    // такую базу они должны отвергнуть, а не упасть на ней (на ПК версию
    // поднимает открытие базы).
    if (db.get<{ user_version: number }>('PRAGMA user_version')!.user_version < SCHEMA_VERSION) {
      db.exec(`PRAGMA user_version = ${SCHEMA_VERSION}`);
    }
    const startedAt = clock.now().toSql();
    sessionId = db.run(
      'INSERT INTO import_sessions (source_file_name, source_file_sha256, started_at, ' +
        'finished_at, total_rows, kty_rows, matched_rows, new_to_ship_rows, already_on_point, ' +
        'returned_rows, not_ours_rows, errors_rows, log_json) ' +
        'VALUES (?, ?, ?, NULL, 0, 0, 0, 0, 0, 0, 0, 0, NULL)',
      [fileName, sha256, startedAt],
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

    // Дубль — выданная посылка, которой не было в прошлом отчёте, а теперь она
    // снова пришла. Если прошлый отчёт неполный, пропуск только в нём не в счёт.
    const [previousId, beforePreviousId] = previousSessions(db, sessionId);
    const [lost, kept] = missingInPrevious(db, rows, previousId, beforePreviousId);
    const previousIncomplete = isIncomplete(lost, kept);

    const seenInFile = new Set<string>();
    for (const row of rows) {
      const key = fileKey(row);
      if (seenInFile.has(key)) continue;
      seenInFile.add(key);

      const existing = db.get<ShipmentState>(
        'SELECT id, assignment_status, exported_import_session_id, last_seen_import_session_id, ' +
          'last_seen_at FROM shipments WHERE posting_number = ? AND product_label = ?',
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
        // До touch: он перезапишет отметку последнего появления, по которой виден пропуск.
        let reappeared = false;
        if (isReady && isIssued(existing) && (existing.last_seen_import_session_id ?? 0) < previousId) {
          if (previousIncomplete && existing.last_seen_import_session_id === beforePreviousId) {
            skippedDuplicates++;
          } else {
            reappeared = true;
          }
        }
        const previousSeenAt = existing.last_seen_at;

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
        } else if (reappeared) {
          // Выданная посылка снова пришла: тот же номер, штрихкод и клиент.
          const days = daysBetween(startedAt, previousSeenAt);
          db.run(
            'UPDATE shipments SET assigned_point = ?, ozon_status = ?, first_seen_at = last_seen_at ' +
              'WHERE id = ?',
            [client.fixed_delivery_point, status, existing.id],
          );
          if (days <= DUPLICATE_DAYS) {
            // До решения оператора в отгрузку не идёт.
            db.run('UPDATE shipments SET assignment_status = ?, delivered_at = ? WHERE id = ?',
              [AssignmentStatus.DUPLICATE, previousSeenAt, existing.id]);
            logs.push(`DUPLICATE: Shipment ${pyStr(row.posting_number)} was issued and reappeared in import.`);
          } else {
            // Давно выдана — это уже новая посылка.
            db.run(
              'UPDATE shipments SET assignment_status = ?, exported_import_session_id = NULL, ' +
                'delivered_at = NULL WHERE id = ?',
              [AssignmentStatus.TO_SHIP, existing.id],
            );
            counters.new_to_ship_rows++;
            logs.push(
              `Shipment ${pyStr(row.posting_number)} reappeared ${days} days after issue, shipping as new.`,
            );
          }
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
          // Ещё не привезена или дубль без решения: точка — актуальная у клиента.
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

    if (previousIncomplete) {
      logs.push(
        `WARNING: Previous import looks incomplete: ${lost} of ${lost + kept} shipments missing. ` +
          `Not treated as duplicates: ${skippedDuplicates}.`,
      );
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
  return {
    session: db.get<ImportSessionRow>('SELECT * FROM import_sessions WHERE id = ?', [sessionId])!,
    skippedDuplicates,
  };
}
