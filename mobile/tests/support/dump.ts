/**
 * Сравнение баз ПК и телефона: все таблицы построчно с typeof() каждого значения.
 * Метки времени заменяются названием шага теста, в окно которого они попали, —
 * значения времени у ПК и телефона разные, а важен сам момент записи.
 */
import { openBunDb } from './bunDb';

const TIMESTAMP_COLUMNS = new Set([
  'started_at', 'finished_at', 'first_seen_at', 'last_seen_at',
  'created_at', 'updated_at', 'export_date',
]);
const TS_RE = /^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{6})$/;

export class StepClock {
  private readonly windows: Array<{ step: string; from: number; to: number }> = [];

  /** Выполнить fn как шаг: всё время, записанное внутри, помечается его именем. */
  run<T>(step: string, fn: () => T): T {
    const from = Date.now();
    try {
      return fn();
    } finally {
      this.windows.push({ step, from, to: Date.now() });
    }
  }

  label(value: string): string {
    const m = TS_RE.exec(value);
    if (!m) return `bad-format:${value}`;
    const ms = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], Math.floor(+m[7] / 1000)).getTime();
    const hit = this.windows.find((w) => ms >= w.from - 1 && ms <= w.to + 1);
    // Время вне шагов (заданное фикстурой) сравнивается как есть.
    return hit ? `@${hit.step}` : value;
  }
}

export type Dump = Record<string, Array<Record<string, [string, unknown]>>>;

export function dumpDb(path: string, clock: StepClock): Dump {
  const db = openBunDb(path);
  try {
    const out: Dump = {};
    for (const table of ['clients', 'import_sessions', 'shipments', 'export_sessions']) {
      const cols = db.all<{ name: string }>(`PRAGMA table_info(${table})`).map((c) => c.name);
      const select = cols.map((c) => `typeof(${c}) AS "t_${c}", ${c} AS "v_${c}"`).join(', ');
      out[table] = db.all<Record<string, unknown>>(`SELECT ${select} FROM ${table} ORDER BY id`)
        .map((row) => Object.fromEntries(cols.map((c) => {
          const v = row[`v_${c}`];
          const shown = TIMESTAMP_COLUMNS.has(c) && typeof v === 'string' ? clock.label(v) : v;
          return [c, [row[`t_${c}`] as string, shown]];
        })));
    }
    return out;
  } finally {
    db.close();
  }
}
