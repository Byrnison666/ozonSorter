/**
 * Разбор отчёта склада Ozon — перенос src/parser.py (ExcelParser) один в один.
 * Совпадение с ПК проверяет tests/parser.test.ts на общих xlsx.
 */
import {
  type CellValue, fromExcel, pyLstripZeros, pyStr, pyStrip, pyTruthy,
} from './py';

export const SIGNATURE_COLUMNS = ['Номер отправления', 'Ячейка'] as const;
export const READY_STATUS = 'Готово к выдаче';

export const COLUMN_MAP: ReadonlyArray<readonly [string, LogicalColumn]> = [
  ['Этикетка\nНазвание', 'label_and_name'],
  ['Номер отправления', 'posting_number'],
  ['Тип', 'type'],
  ['Статус', 'status'],
  ['Ячейка', 'cell'],
  ['Отсчётная дата отправки', 'shipment_date_ozon'],
  ['Перевозка', 'carriage'],
  ['Контейнер\nШтрихкод', 'container_barcode'],
];

export type LogicalColumn =
  | 'label_and_name' | 'posting_number' | 'type' | 'status' | 'cell'
  | 'shipment_date_ozon' | 'carriage' | 'container_barcode';

/**
 * Строка отчёта. Колонки из COLUMN_MAP присутствуют только если есть в шапке —
 * «нет колонки» и «пустая ячейка» различаются, как ключ в dict на ПК.
 */
export type ParsedRow = Partial<Record<LogicalColumn, CellValue>> & {
  posting_number: CellValue;
  product_label: string;
  product_name: string | null;
  is_damaged: boolean;
  ozon_client_id: string | null;
  is_kty: boolean;
};

export class ReportParseError extends Error {}

/** Канонический Ozon ID: без ведущих нулей (normalize_ozon_id). */
export function normalizeOzonId(ozonId: CellValue | undefined): string {
  return pyLstripZeros(pyStrip(pyStr(ozonId))) || '0';
}

/** Готова ли посылка к выдаче (is_ready_for_pickup). */
export function isReadyForPickup(status: CellValue | undefined, cell?: CellValue): boolean {
  if (status === null || status === undefined || pyStrip(pyStr(status)) === '') {
    const normalizedCell = pyStrip(pyTruthy(cell) ? pyStr(cell) : '').toLowerCase();
    return !normalizedCell.startsWith('на проверку');
  }
  return pyStrip(pyStr(status)).toLowerCase() === READY_STATUS.toLowerCase();
}

// \d в Python — любые десятичные цифры Unicode.
const CLIENT_ID_RE = /^(\p{Nd}+)-/u;

/** Ozon ID клиента — всё до первого дефиса (extract_ozon_client_id). */
export function extractOzonClientId(postingNumber: string): string | null {
  if (!postingNumber) return null;
  const match = CLIENT_ID_RE.exec(pyStrip(postingNumber));
  return match ? match[1] : null;
}

function normalizeHeader(value: CellValue): string {
  return pyStr(value).toLowerCase().replaceAll(' ', '').replaceAll('\n', '');
}

function findHeader(grid: CellValue[][]): [number, Map<LogicalColumn, number>] | null {
  const signatures = SIGNATURE_COLUMNS.map((s) => normalizeHeader(s));
  for (let r = 0; r < Math.min(grid.length, 20); r++) {
    const row = grid[r];
    const normalized = row.map((c) => (pyTruthy(c) ? normalizeHeader(c) : ''));
    if (!signatures.every((sig) => normalized.includes(sig))) continue;
    const mapping = new Map<LogicalColumn, number>();
    row.forEach((cellVal, colIdx) => {
      if (!pyTruthy(cellVal)) return;
      const norm = normalizeHeader(cellVal);
      for (const [header, logical] of COLUMN_MAP) {
        if (normalizeHeader(header) === norm) mapping.set(logical, colIdx);
      }
    });
    return [r, mapping];
  }
  return null;
}

/** parse_file: строки отчёта из таблицы активного листа. */
export function parseReport(grid: CellValue[][]): ParsedRow[] {
  const header = findHeader(grid);
  if (!header) {
    throw new ReportParseError(
      'Не удалось найти заголовок таблицы. Обязательные колонки: ' + SIGNATURE_COLUMNS.join(', '),
    );
  }
  const [headerIdx, mapping] = header;
  const postingIdx = mapping.get('posting_number');
  if (postingIdx === undefined) return [];

  const results: ParsedRow[] = [];
  for (const row of grid.slice(headerIdx + 1)) {
    const postingNumber = row[postingIdx] ?? null;
    if (!pyTruthy(postingNumber)) continue;

    const fields: Partial<Record<LogicalColumn, CellValue>> = {};
    for (const [logical, colIdx] of mapping) {
      let val: CellValue = row[colIdx] ?? null;
      // bool в Python — тоже int, поэтому попадает в эту ветку.
      if (logical === 'shipment_date_ozon' && (typeof val === 'number' || typeof val === 'boolean')) {
        try {
          val = fromExcel(Number(val));
        } catch {
          val = null;
        }
      }
      fields[logical] = val;
    }

    let productLabel: string | null = null;
    let productName: string | null = null;
    if (pyTruthy(fields.label_and_name)) {
      const parts = pyStr(fields.label_and_name).split('\n');
      productLabel = pyStrip(parts[0]);
      productName = parts.length > 1 ? pyStrip(parts[1]) : null;
    }
    // Штрихкод — часть ключа посылки, пустым быть не должен (см. parser.py).
    if (!productLabel) productLabel = pyStrip(pyStr(postingNumber));

    const typeText = 'type' in fields ? pyStr(fields.type) : '';
    const ozonClientId = extractOzonClientId(pyStr(postingNumber));

    results.push({
      ...fields,
      posting_number: postingNumber,
      product_label: productLabel,
      product_name: productName,
      is_damaged: typeText.includes('Повреждено'),
      ozon_client_id: ozonClientId,
      is_kty: ozonClientId === null,
    });
  }
  return results;
}
