/**
 * Поведение Python, от которого зависят данные в базе: телефон пишет в ту же
 * базу, что и ПК, и должен давать те же строки байт в байт (str(), strip(),
 * from_excel openpyxl, формат DateTime SQLAlchemy, json.dumps).
 */

/** Наивные дата и время (без часового пояса), как datetime в Python. */
export class PyDateTime {
  constructor(
    readonly year: number,
    readonly month: number,
    readonly day: number,
    readonly hour = 0,
    readonly minute = 0,
    readonly second = 0,
    readonly microsecond = 0,
  ) {}

  static fromDate(d: Date): PyDateTime {
    return new PyDateTime(
      d.getFullYear(), d.getMonth() + 1, d.getDate(),
      d.getHours(), d.getMinutes(), d.getSeconds(), d.getMilliseconds() * 1000,
    );
  }

  static now(): PyDateTime {
    return PyDateTime.fromDate(new Date());
  }

  private datePart(): string {
    return `${pad(this.year, 4)}-${pad(this.month, 2)}-${pad(this.day, 2)}`;
  }

  private timePart(): string {
    return `${pad(this.hour, 2)}:${pad(this.minute, 2)}:${pad(this.second, 2)}`;
  }

  /** str(datetime) */
  toString(): string {
    const micro = this.microsecond ? `.${pad(this.microsecond, 6)}` : '';
    return `${this.datePart()} ${this.timePart()}${micro}`;
  }

  /** Как SQLAlchemy хранит DateTime в SQLite: микросекунды всегда. */
  toSql(): string {
    return `${this.datePart()} ${this.timePart()}.${pad(this.microsecond, 6)}`;
  }
}

/** Время без даты — openpyxl отдаёт его для ячеек-дат со значением меньше суток. */
export class PyTime {
  constructor(
    readonly hour: number,
    readonly minute: number,
    readonly second: number,
    readonly microsecond = 0,
  ) {}

  toString(): string {
    const micro = this.microsecond ? `.${pad(this.microsecond, 6)}` : '';
    return `${pad(this.hour, 2)}:${pad(this.minute, 2)}:${pad(this.second, 2)}${micro}`;
  }
}

/** Значение ячейки так, как его отдаёт openpyxl (values_only, data_only=True). */
export type CellValue = string | number | boolean | PyDateTime | PyTime | null;

function pad(n: number, width: number): string {
  return String(n).padStart(width, '0');
}

/** Пробельные символы str.strip() в Python (у JS trim() набор другой). */
const PY_WS = '\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const STRIP_RE = new RegExp(`^[${PY_WS}]+|[${PY_WS}]+$`, 'g');
const LSTRIP_ZEROS_RE = /^0+/;

export function pyStrip(s: string): string {
  return s.replace(STRIP_RE, '');
}

export function pyLstripZeros(s: string): string {
  return s.replace(LSTRIP_ZEROS_RE, '');
}

/**
 * repr(float). Целые значения openpyxl отдаёт как int, если в XML нет точки;
 * здесь различить int и float по значению нельзя, поэтому целое число всегда
 * печатается как int (Excel пишет целые без точки — расхождение только теоретическое).
 */
function pyNumberStr(n: number): string {
  if (Number.isInteger(n)) return String(n);
  if (!Number.isFinite(n)) return Number.isNaN(n) ? 'nan' : n > 0 ? 'inf' : '-inf';
  const abs = Math.abs(n);
  if (abs >= 1e16 || abs < 1e-4) {
    // Python: мантисса + 'e' + знак + минимум две цифры порядка.
    const [mantissa, exp] = n.toExponential().split('e');
    const sign = exp.startsWith('-') ? '-' : '+';
    return `${mantissa}e${sign}${exp.replace(/^[+-]/, '').padStart(2, '0')}`;
  }
  return String(n);
}

/** str(value) для значений ячеек. */
export function pyStr(value: CellValue | undefined): string {
  if (value === null || value === undefined) return 'None';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (typeof value === 'number') return pyNumberStr(value);
  return value.toString();
}

/** Истинность значения в Python (if value: ...). */
export function pyTruthy(value: CellValue | undefined): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.length > 0;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'boolean') return value;
  return true;
}

/** round() Python 3: половина — к чётному. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 ? 2 * Math.round(x / 2) : r;
}

const MS_PER_DAY = 86_400_000;
const WINDOWS_EPOCH_MS = Date.UTC(1899, 11, 30);
const MAC_EPOCH_MS = Date.UTC(1904, 0, 1);
// Границы datetime в Python. Date.UTC для годов 0–99 прибавляет 1900 — берём ISO.
const MIN_MS = new Date('0001-01-01T00:00:00Z').getTime();
const MAX_MS = new Date('9999-12-31T23:59:59.999Z').getTime();

export class PyOverflowError extends Error {}

/** openpyxl.utils.datetime.from_excel (3.1.5), без ветки timedelta. */
export function fromExcel(value: number, date1904 = false): PyDateTime | PyTime {
  const day = Math.floor(value);
  const fraction = value - day;
  const diffMs = pyRound(fraction * MS_PER_DAY);
  if (value >= 0 && value < 1 && diffMs < MS_PER_DAY) {
    const t = new Date(diffMs);
    return new PyTime(t.getUTCHours(), t.getUTCMinutes(), t.getUTCSeconds(), t.getUTCMilliseconds() * 1000);
  }
  let days = day;
  if (value > 0 && value < 60 && !date1904) days += 1;
  const ms = (date1904 ? MAC_EPOCH_MS : WINDOWS_EPOCH_MS) + days * MS_PER_DAY + diffMs;
  if (!(ms >= MIN_MS && ms <= MAX_MS)) throw new PyOverflowError('date value out of range');
  const d = new Date(ms);
  return new PyDateTime(
    d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate(),
    d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds(), d.getUTCMilliseconds() * 1000,
  );
}

/** json.dumps(list_of_str) с настройками по умолчанию (ensure_ascii, ", "). */
export function pyJsonDumpsStrings(items: readonly string[]): string {
  return `[${items.map(pyJsonString).join(', ')}]`;
}

function pyJsonString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    const code = s.charCodeAt(i);
    if (ch === '"') out += '\\"';
    else if (ch === '\\') out += '\\\\';
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (ch === '\b') out += '\\b';
    else if (ch === '\f') out += '\\f';
    else if (code < 0x20 || code > 0x7e) out += `\\u${code.toString(16).padStart(4, '0')}`;
    else out += ch;
  }
  return `${out}"`;
}
