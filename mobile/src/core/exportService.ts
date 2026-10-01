/**
 * Выгрузка «Отгрузка <точка>» — перенос ExportService (src/export_service.py).
 * Две фазы, как на ПК: файл сохраняется до отметки посылок в базе, поэтому
 * сбой записи файла базу не меняет.
 */
import type { Db } from './db';
import { AssignmentStatus, type Clock, type DeliveryPoint, systemClock } from './models';
import { compareKeys, naturalKey } from './naturalSort';
import { PyDateTime } from './py';
import { type CellStyle, type WriteCell, writeXlsx } from './xlsxWriter';

export class ExportError extends Error {}

interface ExportShipment {
  id: number;
  posting_number: string;
  product_label: string | null;
  product_name: string | null;
  cell: string | null;
  is_damaged: number;
}

export interface PreparedExport {
  point: DeliveryPoint;
  importSessionId: number;
  shipmentIds: number[];
  /** Содержимое xlsx. */
  bytes: Uint8Array;
}

const DAMAGED_FILL = 'FFE5E5';
const WIDTHS = [60, 22, 20, 18, 14];

/** Имя файла по умолчанию, как в диалоге сохранения на ПК. */
export function defaultExportFileName(point: DeliveryPoint, clock: Clock = systemClock): string {
  const now = clock.now();
  const date = `${String(now.day).padStart(2, '0')}.${String(now.month).padStart(2, '0')}.${now.year}`;
  return `Отгрузка_${point === 'KOMSOMOLSKAYA_4' ? 'Комсомольская' : 'Кольцевая'}_${date}.xlsx`;
}

function sheetTitle(now: PyDateTime): string {
  return `${String(now.day).padStart(2, '0')},${String(now.month).padStart(2, '0')}`;
}

/** Собрать файл выгрузки. База не меняется. */
export function prepareExport(
  db: Db, point: DeliveryPoint, importSessionId: number, clock: Clock = systemClock,
): PreparedExport {
  if (!db.get('SELECT id FROM import_sessions WHERE id = ?', [importSessionId])) {
    throw new ExportError(`Import session ${importSessionId} not found`);
  }
  // Только посылки текущего отчёта; уже выгруженные в прошлых сессиях — нет,
  // выгруженные в этой же — да (повторная выгрузка воспроизводит файл).
  const shipments = db.all<ExportShipment>(
    'SELECT id, posting_number, product_label, product_name, cell, is_damaged FROM shipments ' +
      'WHERE assigned_point = ? AND assignment_status = ? AND last_seen_import_session_id = ? ' +
      'AND (exported_import_session_id IS NULL OR exported_import_session_id = ?) ORDER BY id',
    [point, AssignmentStatus.TO_SHIP, importSessionId, importSessionId],
  );
  // Array.prototype.sort устойчива — порядок равных ключей как у list.sort.
  const keyed = shipments.map((s) => ({ s, key: naturalKey(s.cell || '') }));
  keyed.sort((a, b) => compareKeys(a.key, b.key));

  const base: CellStyle = { wrap: true, vertical: 'top' };
  const rows = keyed.map(({ s }) => {
    const style = s.is_damaged ? { ...base, fill: DAMAGED_FILL } : base;
    const row: WriteCell[] = [
      // Описание — название; этикетка часто штрихкод или дубль номера.
      { value: s.product_name || s.product_label || s.posting_number, style },
      { value: s.posting_number, style },
      { value: s.product_label, style },
      { value: s.cell, style },
    ];
    if (s.is_damaged) row.push({ value: 'ПОВРЕЖДЕНО', style });
    return row;
  });

  return {
    point,
    importSessionId,
    shipmentIds: keyed.map(({ s }) => s.id),
    bytes: writeXlsx({ name: sheetTitle(clock.now()), rows, widths: WIDTHS }),
  };
}

/** После сохранения файла: отметить посылки выгруженными и записать сессию выгрузки. */
export function commitExport(
  db: Db, prepared: PreparedExport, filePath: string, clock: Clock = systemClock,
): number {
  let exportId = 0;
  db.transaction(() => {
    for (const id of prepared.shipmentIds) {
      db.run('UPDATE shipments SET exported_import_session_id = ? WHERE id = ?',
        [prepared.importSessionId, id]);
    }
    const exportDate = clock.now().toSql();
    const created = clock.now().toSql();
    exportId = db.run(
      'INSERT INTO export_sessions (import_session_id, delivery_point, export_date, file_path, ' +
        'shipments_count, created_at) VALUES (?, ?, ?, ?, ?, ?)',
      [prepared.importSessionId, prepared.point, exportDate, filePath,
        prepared.shipmentIds.length, created],
    ).lastInsertRowId;
  });
  return exportId;
}
