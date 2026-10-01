/**
 * «Залежавшиеся посылки» — перенос StaleShipmentService (src/stale_service.py):
 * посылки наших клиентов из последнего отчёта и сколько дней они в базе.
 */
import type { Db } from './db';
import { AssignmentStatus, type DeliveryPoint, pointLabel } from './models';
import { compareKeys, comparePyStrings, naturalKey } from './naturalSort';
import { type CellStyle, writeXlsx } from './xlsxWriter';

export const WARN_DAYS = 3;   // с этого числа дней строка жёлтая
export const DANGER_DAYS = 7; // с этого — красная

export const STALE_COLUMNS = [
  'Дней в базе', 'Ячейка', 'Номер отправления', 'Штрихкод', 'Товар',
  'Клиент', 'Ozon ID', 'Телефон', 'Точка', 'Впервые в отчёте', 'Статус',
] as const;

const COLUMN_WIDTHS = [12, 12, 24, 22, 50, 28, 14, 18, 18, 18, 18];
const WARN_FILL = 'FFF4D6';
const DANGER_FILL = 'FFE5E5';

export interface StaleRow {
  days: number;
  cell: string;
  posting_number: string;
  product_label: string;
  product_name: string;
  client_name: string;
  ozon_client_id: string;
  phone: string;
  point: string | null;
  /** Как в базе: 'YYYY-MM-DD HH:MM:SS.ffffff'. */
  first_seen_at: string;
  is_returned: boolean;
}

/** Число дней между датами (без времени) двух меток времени из базы. */
function daysBetween(later: string, earlier: string): number {
  const day = (ts: string) => Date.UTC(+ts.slice(0, 4), +ts.slice(5, 7) - 1, +ts.slice(8, 10));
  return Math.round((day(later) - day(earlier)) / 86_400_000);
}

export function firstSeenLabel(row: StaleRow): string {
  const ts = row.first_seen_at;
  return `${ts.slice(8, 10)}.${ts.slice(5, 7)}.${ts.slice(0, 4)}`;
}

/** Значения строки в порядке STALE_COLUMNS (days — число, остальное — строки). */
export function staleCells(row: StaleRow): Array<string | number> {
  return [
    row.days, row.cell, row.posting_number, row.product_label, row.product_name,
    row.client_name, row.ozon_client_id, row.phone, pointLabel(row.point),
    firstSeenLabel(row), row.is_returned ? 'Возврат' : 'Готово к выдаче',
  ];
}

export function latestImport(db: Db): { id: number; started_at: string } | undefined {
  return db.get('SELECT id, started_at FROM import_sessions ORDER BY id DESC LIMIT 1');
}

interface Joined {
  cell: string | null;
  posting_number: string | null;
  product_label: string | null;
  product_name: string | null;
  assigned_point: string | null;
  first_seen_at: string;
  assignment_status: string;
  full_name: string | null;
  ozon_client_id: string | null;
  phone: string | null;
}

export function listStale(db: Db, point: DeliveryPoint | null = null, minDays = 0): StaleRow[] {
  const latest = latestImport(db);
  if (!latest) return [];
  // Дни — до даты последнего отчёта, а не до сегодня: без нового отчёта
  // результат не меняется сам по себе.
  const params: Array<string | number> = [
    latest.id, AssignmentStatus.EXCLUDED_NOT_OURS, AssignmentStatus.EXCLUDED_KTY,
  ];
  let sql =
    'SELECT s.cell, s.posting_number, s.product_label, s.product_name, s.assigned_point, ' +
    's.first_seen_at, s.assignment_status, c.full_name, c.ozon_client_id, c.phone ' +
    'FROM shipments s JOIN clients c ON s.client_id = c.id ' +
    'WHERE s.last_seen_import_session_id = ? AND c.is_active = 1 ' +
    'AND s.assignment_status NOT IN (?, ?)';
  if (point !== null) {
    sql += ' AND s.assigned_point = ?';
    params.push(point);
  }
  sql += ' ORDER BY s.id';

  const rows: StaleRow[] = [];
  for (const r of db.all<Joined>(sql, params)) {
    const days = Math.max(0, daysBetween(latest.started_at, r.first_seen_at));
    if (days < minDays) continue;
    rows.push({
      days,
      cell: r.cell || '',
      posting_number: r.posting_number || '',
      product_label: r.product_label || '',
      product_name: r.product_name || '',
      client_name: r.full_name || '',
      ozon_client_id: r.ozon_client_id || '',
      phone: r.phone || '',
      point: r.assigned_point,
      first_seen_at: r.first_seen_at,
      is_returned: r.assignment_status === AssignmentStatus.RETURNED,
    });
  }
  const keyed = rows.map((r) => ({ r, key: naturalKey(r.cell) }));
  keyed.sort((a, b) => (b.r.days - a.r.days) || compareKeys(a.key, b.key)
    || comparePyStrings(a.r.posting_number, b.r.posting_number));
  return keyed.map(({ r }) => r);
}

export function exportStaleXlsx(rows: StaleRow[]): Uint8Array {
  const align: CellStyle = { wrap: true, vertical: 'top' };
  const header = STALE_COLUMNS.map((value) => ({ value, style: { ...align, bold: true } }));
  const body = rows.map((row) => {
    const fill = row.days >= DANGER_DAYS ? DANGER_FILL : row.days >= WARN_DAYS ? WARN_FILL : undefined;
    const style = fill ? { ...align, fill } : align;
    return staleCells(row).map((value) => ({ value, style }));
  });
  return writeXlsx({ name: 'Залежавшиеся', rows: [header, ...body], widths: COLUMN_WIDTHS, freezeRows: 1 });
}
