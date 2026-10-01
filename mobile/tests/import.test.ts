import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { createHash } from 'node:crypto';
import { copyFileSync, readFileSync } from 'node:fs';
import { basename, join } from 'node:path';

import type { Db } from '../src/core/db';
import { findDuplicateImport, ImportError, processImport } from '../src/core/importService';
import { parseReport } from '../src/core/parser';
import { configureConnection, ensureSchema } from '../src/core/schema';
import { readActiveSheet } from '../src/core/sheet';
import { openBunDb } from './support/bunDb';
import { dumpDb, StepClock } from './support/dump';
import { runPythonScript, tempDir } from './support/pc';

const clock = new StepClock();
const dump = (path: string) => dumpDb(path, clock);

function withPhone<T>(path: string, fn: (db: Db) => T): T {
  const db = openBunDb(path);
  configureConnection(db);
  ensureSchema(db);
  try {
    return fn(db);
  } finally {
    db.close();
  }
}

describe('импорт отчёта совпадает с ПК', () => {
  const dir = tempDir('ozon-import-');
  const pcDb = join(dir.path, 'pc.db');
  const phoneDb = join(dir.path, 'phone.db');
  const report = (n: string) => join(dir.path, n);

  function importBoth(step: string, file: string) {
    return clock.run(step, () => {
      const pc = JSON.parse(runPythonScript('import_step.py', pcDb, file));
      let phone: { session_id?: number; error?: string };
      try {
        const bytes = new Uint8Array(readFileSync(file));
        const sha = createHash('sha256').update(bytes).digest('hex');
        phone = withPhone(phoneDb, (db) => ({
          session_id: processImport(db, basename(file), sha, parseReport(readActiveSheet(bytes))).id,
        }));
      } catch (e) {
        if (!(e instanceof ImportError)) throw e;
        phone = { error: e.message };
      }
      return { pc, phone };
    });
  }

  function editBoth(sql: string) {
    for (const path of [pcDb, phoneDb]) {
      const db = openBunDb(path);
      db.exec(sql);
      db.close();
    }
  }

  beforeAll(() => {
    runPythonScript('import_fixtures.py', dir.path);
    copyFileSync(join(dir.path, 'base.db'), pcDb);
    copyFileSync(join(dir.path, 'base.db'), phoneDb);
  });
  afterAll(() => dir.cleanup());

  test('первый отчёт', () => {
    const { pc, phone } = importBoth('import1', report('report1.xlsx'));
    expect(phone).toEqual(pc);
    expect(dump(phoneDb)).toEqual(dump(pcDb));
  });

  test('второй отчёт после правок оператора', () => {
    editBoth(`
      INSERT INTO clients (ozon_client_id, full_name, phone, fixed_delivery_point, notes,
        is_active, created_at, updated_at)
      VALUES ('888000333', 'Евгений', NULL, 'KOMSOMOLSKAYA_4', NULL, 1,
        '2026-09-01 10:00:00.000000', '2026-09-01 10:00:00.000000');
      UPDATE shipments SET assignment_status = 'ON_POINT' WHERE posting_number = '147012251-0002-3';
      UPDATE shipments SET assignment_status = 'DELIVERED' WHERE posting_number = '147012251-0002-1';
      UPDATE clients SET fixed_delivery_point = 'KOLTSEVAYA_16' WHERE ozon_client_id = '224933356';
      UPDATE shipments SET exported_import_session_id = 1 WHERE posting_number = '0224933356-0001-1';
    `);
    const { pc, phone } = importBoth('import2', report('report2.xlsx'));
    expect(phone).toEqual(pc);
    expect(dump(phoneDb)).toEqual(dump(pcDb));
  });

  test('текстовая дата отправки: оба отказывают, база не меняется', () => {
    const before = { pc: dump(pcDb), phone: dump(phoneDb) };
    const { pc, phone } = importBoth('import3', report('report3_bad_date.xlsx'));
    expect(pc.error).toContain('DateTime');
    expect(phone.error).toBeDefined();
    expect(dump(pcDb)).toEqual(before.pc);
    expect(dump(phoneDb)).toEqual(before.phone);
  });

  test('повторный импорт того же файла', () => {
    const sha = createHash('sha256').update(readFileSync(report('report1.xlsx'))).digest('hex');
    expect(withPhone(phoneDb, (db) => findDuplicateImport(db, sha)?.id)).toBe(1);
    const { pc, phone } = importBoth('import4', report('report1.xlsx'));
    expect(phone).toEqual(pc);
    expect(dump(phoneDb)).toEqual(dump(pcDb));
  });

  test('в эталоне есть содержательные случаи', () => {
    const shipments = dump(pcDb).shipments as Array<Record<string, [string, unknown]>>;
    const statuses = new Set(shipments.map((s) => s.assignment_status[1]));
    for (const s of ['TO_SHIP', 'RETURNED', 'ON_POINT', 'DELIVERED', 'EXCLUDED_NOT_OURS', 'EXCLUDED_KTY']) {
      expect(statuses).toContain(s);
    }
    // Время последней встречи сдвигается повторным импортом.
    expect(shipments.some((s) => s.last_seen_at[1] === '@import2')).toBe(true);
  });
});
