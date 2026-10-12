/**
 * Дубли — перенос src/duplicate_service.py: выданная посылка снова пришла в
 * отчёте склада. До решения оператора в отгрузку не идёт.
 */
import type { Db } from './db';
import { AssignmentStatus } from './models';
import { compareKeys, comparePyStrings, naturalKey } from './naturalSort';
import { latestImport } from './staleService';

// Выданная посылка снова пришла в отчёте склада не позже стольких дней после
// выдачи — это дубль. Позже — новая посылка, идёт в отгрузку как обычно.
export const DUPLICATE_DAYS = 30;

// Прошлый отчёт считаем неполным, если из него выпало не меньше INCOMPLETE_MIN
// посылок и не меньше INCOMPLETE_PERCENT % от тех, что есть и в текущем отчёте,
// и в позапрошлом. Пропуск только в таком отчёте выдачей не считается.
export const INCOMPLETE_MIN = 5;
export const INCOMPLETE_PERCENT = 5;

/** Посылку уже забрали со склада: попала в выгрузку «Отгрузки», выдана или уже дубль. */
export function isIssued(s: { assignment_status: string; exported_import_session_id: number | null }): boolean {
  if (s.assignment_status === AssignmentStatus.TO_SHIP) return s.exported_import_session_id !== null;
  return s.assignment_status === AssignmentStatus.DELIVERED || s.assignment_status === AssignmentStatus.DUPLICATE;
}

/** lost — нет в прошлом отчёте, но есть в позапрошлом и текущем; kept — есть в прошлом и текущем. */
export function isIncomplete(lost: number, kept: number): boolean {
  return lost >= INCOMPLETE_MIN && lost * 100 >= INCOMPLETE_PERCENT * (lost + kept);
}

/** Текст предупреждения о дублях (после импорта и на «Главной»). */
export function duplicatesAlertText(count: number): string {
  return `В отчёте снова пришли посылки, которые уже выдавали: ${count}.\n` +
    'Тот же номер отправления и штрихкод, тот же клиент — это дубли. ' +
    'В отгрузку они не попадут, пока вы не решите в разделе «Дубли»: ' +
    'отгрузить или отметить как ошибку Ozon.';
}

/** Предупреждение после импорта: прошлый отчёт был неполным. */
export function incompleteReportText(skipped: number): string {
  return 'Прошлый отчёт, похоже, был неполным: в нём не хватало многих посылок. ' +
    `Выданных посылок, которых не было только в нём: ${skipped} — дублями их не считаю.`;
}

export interface DuplicateRow {
  shipment_id: number;
  posting_number: string;
  product_label: string;
  product_name: string;
  client_name: string;
  ozon_client_id: string;
  phone: string;
  point: string | null;
  cell: string;
  /** Последний отчёт, в котором посылка была до выдачи ('YYYY-MM-DD HH:MM:SS.ffffff'). */
  delivered_at: string | null;
  /** Отчёт, в котором она пришла снова. */
  reappeared_at: string;
}

/** Дата метки времени из базы как '%d.%m.%Y'; нет метки — «—». */
export function dateLabel(ts: string | null): string {
  return ts ? `${ts.slice(8, 10)}.${ts.slice(5, 7)}.${ts.slice(0, 4)}` : '—';
}

interface Joined {
  id: number;
  posting_number: string | null;
  product_label: string | null;
  product_name: string | null;
  assigned_point: string | null;
  cell: string | null;
  delivered_at: string | null;
  first_seen_at: string;
  full_name: string | null;
  ozon_client_id: string | null;
  phone: string | null;
}

/** Дубли из последнего отчёта, ждущие решения оператора. */
export function listDuplicates(db: Db): DuplicateRow[] {
  const latest = latestImport(db);
  if (!latest) return [];
  const rows = db.all<Joined>(
    'SELECT s.id, s.posting_number, s.product_label, s.product_name, s.assigned_point, s.cell, ' +
      's.delivered_at, s.first_seen_at, c.full_name, c.ozon_client_id, c.phone ' +
      'FROM shipments s LEFT JOIN clients c ON s.client_id = c.id ' +
      'WHERE s.assignment_status = ? AND s.last_seen_import_session_id = ? ORDER BY s.id',
    [AssignmentStatus.DUPLICATE, latest.id],
  ).map((r): DuplicateRow => ({
    shipment_id: r.id,
    posting_number: r.posting_number || '',
    product_label: r.product_label || '',
    product_name: r.product_name || '',
    client_name: r.full_name || '',
    ozon_client_id: r.ozon_client_id || '',
    phone: r.phone || '',
    point: r.assigned_point,
    cell: r.cell || '',
    delivered_at: r.delivered_at,
    reappeared_at: r.first_seen_at,
  }));
  const keyed = rows.map((r) => ({ r, key: naturalKey(r.cell) }));
  keyed.sort((a, b) => compareKeys(a.key, b.key)
    || comparePyStrings(a.r.posting_number, b.r.posting_number)
    || comparePyStrings(a.r.product_label, b.r.product_label));
  return keyed.map(({ r }) => r);
}

/**
 * «Отгрузить»: дубль — настоящая новая посылка, в «Отгрузку» последнего отчёта.
 * false — посылка уже не дубль (решили на другом устройстве).
 */
export function shipDuplicate(db: Db, shipmentId: number): boolean {
  return db.run(
    'UPDATE shipments SET assignment_status = ?, exported_import_session_id = NULL ' +
      'WHERE id = ? AND assignment_status = ?',
    [AssignmentStatus.TO_SHIP, shipmentId, AssignmentStatus.DUPLICATE],
  ).changes > 0;
}

/**
 * «Ошибка Ozon»: посылка выдана, в отгрузку не идёт. Пропадёт из отчёта и
 * вернётся в пределах DUPLICATE_DAYS — снова станет дублем.
 */
export function dismissDuplicate(db: Db, shipmentId: number): boolean {
  return db.run(
    'UPDATE shipments SET assignment_status = ? WHERE id = ? AND assignment_status = ?',
    [AssignmentStatus.DELIVERED, shipmentId, AssignmentStatus.DUPLICATE],
  ).changes > 0;
}
