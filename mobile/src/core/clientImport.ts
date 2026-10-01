/**
 * Массовый импорт клиентов из xlsx — перенос ClientImportService
 * (src/client_import_service.py). Невалидная строка не валит файл: ошибки
 * собираются с номерами строк. Существующий клиент (по Ozon ID) обновляется.
 */
import type { Db } from './db';
import { type Clock, DeliveryPoint, systemClock } from './models';
import { normalizeOzonId } from './parser';
import { isDigits } from './clients';
import { type CellValue, pyStr, pyStrip } from './py';
import { writeXlsx } from './xlsxWriter';

type Logical = 'ozon_client_id' | 'full_name' | 'phone' | 'point';

const HEADER_ALIASES: ReadonlyArray<readonly [Logical, ReadonlySet<string>]> = [
  ['ozon_client_id', new Set(['ozonid', 'id', 'озонid', 'озонид'])],
  ['full_name', new Set(['фио', 'имя', 'name'])],
  ['phone', new Set(['телефон', 'phone', 'тел'])],
  ['point', new Set(['точка', 'точкаповыдаче', 'точкапоумолчанию', 'point'])],
];

const POINT_ALIASES: Readonly<Record<string, DeliveryPoint>> = {
  'комсомольская4': DeliveryPoint.KOMSOMOLSKAYA_4,
  'комсомольская': DeliveryPoint.KOMSOMOLSKAYA_4,
  'кольцевая16': DeliveryPoint.KOLTSEVAYA_16,
  'кольцевая': DeliveryPoint.KOLTSEVAYA_16,
};

const REQUIRED: readonly Logical[] = ['ozon_client_id', 'point'];

export const TEMPLATE_HEADERS = ['Ozon ID', 'ФИО', 'Телефон', 'Точка'] as const;

/** Шаблон файла клиентов (write_template). */
export function writeClientTemplate(): Uint8Array {
  const row = (values: string[]) => values.map((value) => ({ value }));
  return writeXlsx({
    name: 'Клиенты',
    rows: [
      row([...TEMPLATE_HEADERS]),
      row(['0224933356', 'Иванов И.И.', '', 'Комсомольская 4']),
      row(['0301234567', 'Петров П.П.', '', 'Кольцевая 16']),
    ],
    widths: [16, 22, 16, 20],
  });
}

export interface ClientImportResult {
  added: number;
  updated: number;
  data_rows: number;
  /** [номер строки в файле, сообщение] */
  errors: Array<[number, string]>;
}

export class ClientImportError extends Error {}

interface Parsed {
  ozon_client_id: string;
  full_name: string | null;
  phone: string | null;
  point: DeliveryPoint;
}

interface ExistingClient {
  id: number;
  ozon_client_id: string;
  full_name: string | null;
  phone: string | null;
  fixed_delivery_point: string | null;
  is_active: number;
}

function norm(value: CellValue | undefined): string {
  if (value === null || value === undefined) return '';
  return pyStrip(pyStr(value)).toLowerCase().replaceAll(' ', '').replaceAll('\n', '');
}

/** _cell_to_str: числа из Excel (id, телефон) — без «.0». */
function cellToStr(value: CellValue | undefined): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number' && Number.isInteger(value)) return pyStr(value);
  // bool в Python — int: str(True) без strip.
  if (typeof value === 'boolean') return pyStr(value);
  return pyStrip(pyStr(value));
}

function findHeader(grid: CellValue[][]): [number, Map<Logical, number>] | null {
  for (let r = 0; r < Math.min(grid.length, 10); r++) {
    const colMap = new Map<Logical, number>();
    grid[r].forEach((cell, colIdx) => {
      const n = norm(cell);
      if (!n) return;
      for (const [logical, aliases] of HEADER_ALIASES) {
        if (aliases.has(n) && !colMap.has(logical)) colMap.set(logical, colIdx);
      }
    });
    if (REQUIRED.every((req) => colMap.has(req))) return [r, colMap];
  }
  return null;
}

function parseRow(raw: Map<Logical, CellValue>): Parsed | string {
  const ozonId = cellToStr(raw.get('ozon_client_id'));
  if (!isDigits(ozonId)) return `Ozon ID «${ozonId}» — только цифры, без дефисов и букв`;

  const pointRaw = norm(raw.get('point'));
  if (!pointRaw) return 'Не указана точка («Комсомольская 4» или «Кольцевая 16»)';
  const point = Object.hasOwn(POINT_ALIASES, pointRaw) ? POINT_ALIASES[pointRaw] : undefined;
  if (point === undefined) {
    return `Точка «${pyStr(raw.get('point'))}» — допустимо «Комсомольская 4» или «Кольцевая 16»`;
  }
  return {
    ozon_client_id: ozonId,
    full_name: cellToStr(raw.get('full_name')) || null,
    phone: cellToStr(raw.get('phone')) || null,
    point,
  };
}

/** import_clients: всё в одной транзакции. */
export function importClients(
  db: Db, grid: CellValue[][], clock: Clock = systemClock,
): ClientImportResult {
  const header = findHeader(grid);
  if (!header) {
    throw new ClientImportError(
      'Не найден заголовок. Обязательные колонки: «Ozon ID» и «Точка». ' +
        'Скачайте шаблон и заполните его.',
    );
  }
  const [headerIdx, colMap] = header;
  const result: ClientImportResult = { added: 0, updated: 0, data_rows: 0, errors: [] };

  db.transaction(() => {
    // Существующие клиенты по нормализованному id; при совпадении — первый.
    const existingByNorm = new Map<string, ExistingClient>();
    for (const c of db.all<ExistingClient>(
      'SELECT id, ozon_client_id, full_name, phone, fixed_delivery_point, is_active ' +
        'FROM clients ORDER BY id',
    )) {
      const n = normalizeOzonId(c.ozon_client_id);
      if (!existingByNorm.has(n)) existingByNorm.set(n, c);
    }
    const seenInFile = new Map<string, [number, Parsed]>();

    for (let r = headerIdx + 1; r < grid.length; r++) {
      const rowNumber = r + 1; // номер строки в Excel
      const raw = new Map<Logical, CellValue>();
      for (const [name, idx] of colMap) {
        if (idx < grid[r].length) raw.set(name, grid[r][idx]);
      }
      if (!cellToStr(raw.get('ozon_client_id'))) continue; // пустая строка

      result.data_rows++;
      const parsed = parseRow(raw);
      if (typeof parsed === 'string') {
        result.errors.push([rowNumber, parsed]);
        continue;
      }

      const n = normalizeOzonId(parsed.ozon_client_id);
      const prev = seenInFile.get(n);
      if (prev) {
        const [prevRow, prevParsed] = prev;
        if (prevParsed.point !== parsed.point || prevParsed.full_name !== parsed.full_name
          || prevParsed.phone !== parsed.phone) {
          result.errors.push([
            rowNumber,
            `Ozon ID ${parsed.ozon_client_id} уже в строке ${prevRow} ` +
              'с другими данными — конфликт, строка пропущена',
          ]);
        }
        continue; // одинаковый дубль строки — молча
      }
      seenInFile.set(n, [rowNumber, parsed]);

      const client = existingByNorm.get(n);
      if (client) {
        const changed = client.ozon_client_id !== n || client.full_name !== parsed.full_name
          || client.phone !== parsed.phone || client.fixed_delivery_point !== parsed.point
          || !client.is_active;
        if (changed) {
          db.run(
            'UPDATE clients SET ozon_client_id = ?, full_name = ?, phone = ?, ' +
              'fixed_delivery_point = ?, is_active = 1, updated_at = ? WHERE id = ?',
            [n, parsed.full_name, parsed.phone, parsed.point, clock.now().toSql(), client.id],
          );
        }
        result.updated++;
      } else {
        const created = clock.now().toSql();
        const updated = clock.now().toSql();
        const id = db.run(
          'INSERT INTO clients (ozon_client_id, full_name, phone, fixed_delivery_point, notes, ' +
            'is_active, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, 1, ?, ?)',
          [n, parsed.full_name, parsed.phone, parsed.point, created, updated],
        ).lastInsertRowId;
        existingByNorm.set(n, {
          id, ozon_client_id: n, full_name: parsed.full_name, phone: parsed.phone,
          fixed_delivery_point: parsed.point, is_active: 1,
        });
        result.added++;
      }
    }
  });
  return result;
}
