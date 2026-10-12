import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { createHash } from 'node:crypto';
import { copyFileSync, readFileSync } from 'node:fs';
import { basename, join } from 'node:path';

import type { Db } from '../src/core/db';
import {
  dateLabel, dismissDuplicate, DUPLICATE_DAYS, duplicatesAlertText, type DuplicateRow,
  INCOMPLETE_MIN, INCOMPLETE_PERCENT, incompleteReportText, isIncomplete, listDuplicates,
  shipDuplicate,
} from '../src/core/duplicates';
import { ImportError, processImport } from '../src/core/importService';
import { pointLabel } from '../src/core/models';
import { parseReport } from '../src/core/parser';
import { configureConnection, ensureSchema, SCHEMA_VERSION } from '../src/core/schema';
import { readActiveSheet } from '../src/core/sheet';
import { listStale } from '../src/core/staleService';
import { openBunDb } from './support/bunDb';
import { dumpDb, StepClock } from './support/dump';
import { runPythonScript, tempDir } from './support/pc';

const clock = new StepClock();
const dump = (path: string) => dumpDb(path, clock);
const ISSUED = ['111-A-1', '111-A-2', '222-B-1', '111-C-1'];

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

function phoneView(r: DuplicateRow) {
  return {
    shipment_id: r.shipment_id, posting_number: r.posting_number,
    product_label: r.product_label, product_name: r.product_name,
    client_name: r.client_name, ozon_client_id: r.ozon_client_id, phone: r.phone,
    point: r.point, point_label: pointLabel(r.point), cell: r.cell,
    delivered_label: dateLabel(r.delivered_at), reappeared_label: dateLabel(r.reappeared_at),
  };
}

type Shipments = Array<Record<string, [string, unknown]>>;

describe('дубли совпадают с ПК', () => {
  const dir = tempDir('ozon-duplicates-');
  const pcDb = join(dir.path, 'pc.db');
  const phoneDb = join(dir.path, 'phone.db');
  const report = (n: string) => join(dir.path, n);

  function importBoth(step: string) {
    const file = report(`${step}.xlsx`);
    return clock.run(step, () => {
      const pc = JSON.parse(runPythonScript('import_step.py', pcDb, file));
      const bytes = new Uint8Array(readFileSync(file));
      const sha = createHash('sha256').update(bytes).digest('hex');
      const phone = withPhone(phoneDb, (db) => {
        const result = processImport(db, basename(file), sha, parseReport(readActiveSheet(bytes)));
        return { session_id: result.session.id, skipped_duplicates: result.skippedDuplicates };
      });
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

  function listBoth() {
    const pc = JSON.parse(runPythonScript('duplicates_step.py', pcDb, 'list'));
    const phone = withPhone(phoneDb, (db) => listDuplicates(db).map(phoneView));
    return { pc, phone };
  }

  function decideBoth(action: 'ship' | 'dismiss', id: number) {
    const pc = JSON.parse(runPythonScript('duplicates_step.py', pcDb, action, String(id)));
    const phone = withPhone(phoneDb, (db) => (action === 'ship' ? shipDuplicate : dismissDuplicate)(db, id));
    return { pc, phone };
  }

  function shipment(posting: string): Record<string, [string, unknown]> {
    return (dump(pcDb).shipments as Shipments).find((s) => s.posting_number[1] === posting)!;
  }

  function lastLog(): string[] {
    const sessions = dump(pcDb).import_sessions as Shipments;
    return JSON.parse(sessions[sessions.length - 1].log_json[1] as string);
  }

  beforeAll(() => {
    runPythonScript('duplicates_fixtures.py', dir.path);
    copyFileSync(join(dir.path, 'base.db'), pcDb);
    copyFileSync(join(dir.path, 'base.db'), phoneDb);
  });
  afterAll(() => dir.cleanup());

  test('выданные посылки снова пришли: дубли и новая посылка после окна', () => {
    let r = importBoth('day1');
    expect(r.phone).toEqual(r.pc);
    editBoth(`UPDATE shipments SET exported_import_session_id = 1
      WHERE posting_number IN (${ISSUED.map((p) => `'${p}'`).join(', ')})`);
    r = importBoth('day2');
    expect(r.phone).toEqual(r.pc);
    // C-1 выдана давно — вернувшись, станет новой посылкой.
    editBoth("UPDATE shipments SET last_seen_at = '2026-01-01 10:00:00.000000' WHERE posting_number = '111-C-1'");
    r = importBoth('day3');
    expect(r.phone).toEqual(r.pc);
    expect(dump(phoneDb)).toEqual(dump(pcDb));

    for (const p of ['111-A-1', '111-A-2', '222-B-1']) {
      expect(shipment(p).assignment_status[1]).toBe('DUPLICATE');
      expect(shipment(p).delivered_at[1]).toBe('@day1');
      expect(shipment(p).first_seen_at[1]).toBe('@day3');
    }
    expect(shipment('111-C-1').assignment_status[1]).toBe('TO_SHIP');
    expect(shipment('111-C-1').exported_import_session_id[1]).toBeNull();
    expect(lastLog()).toContain('DUPLICATE: Shipment 111-A-1 was issued and reappeared in import.');
    expect(lastLog().some((l) => /^Shipment 111-C-1 reappeared \d+ days after issue/.test(l))).toBe(true);

    const list = listBoth();
    expect(list.phone).toEqual(list.pc);
    expect(list.pc.map((d: { posting_number: string }) => d.posting_number))
      .toEqual(['111-A-1', '111-A-2', '222-B-1']);
  });

  test('решения оператора: «Отгрузить» и «Ошибка Ozon»', () => {
    const [a1, , b1] = listBoth().pc as Array<{ shipment_id: number }>;
    expect(decideBoth('ship', a1.shipment_id)).toEqual({ pc: true, phone: true });
    expect(decideBoth('dismiss', b1.shipment_id)).toEqual({ pc: true, phone: true });
    expect(decideBoth('ship', a1.shipment_id)).toEqual({ pc: false, phone: false });
    expect(dump(phoneDb)).toEqual(dump(pcDb));
    expect(shipment('111-A-1').assignment_status[1]).toBe('TO_SHIP');
    expect(shipment('222-B-1').assignment_status[1]).toBe('DELIVERED');

    const list = listBoth();
    expect(list.phone).toEqual(list.pc);
    expect(list.pc.map((d: { posting_number: string }) => d.posting_number)).toEqual(['111-A-2']);
  });

  test('залежавшиеся без дублей и «Ошибки Ozon»', () => {
    const pc = JSON.parse(runPythonScript('stale_step.py', pcDb, '', '0', join(dir.path, 'stale.xlsx')));
    const phone = withPhone(phoneDb, (db) => listStale(db));
    expect(phone.map((r) => r.posting_number)).toEqual(pc.map((r: { posting_number: string }) => r.posting_number));
    expect(pc.map((r: { posting_number: string }) => r.posting_number)).not.toContain('111-A-2');
    expect(pc.map((r: { posting_number: string }) => r.posting_number)).not.toContain('222-B-1');
  });

  test('пропуск только в неполном отчёте дублем не считается', () => {
    let r = importBoth('day4');
    expect(r.phone).toEqual(r.pc);
    r = importBoth('day5');
    expect(r.phone).toEqual(r.pc);
    // A-2 (дубль) и B-1 (ошибка Ozon) не было только в неполном day4; в нём
    // остались NEW-1 и КТЯ — kept 2 из 20.
    expect(r.pc.skipped_duplicates).toBe(2);
    expect(dump(phoneDb)).toEqual(dump(pcDb));
    expect(shipment('111-A-2').assignment_status[1]).toBe('DUPLICATE');
    expect(shipment('222-B-1').assignment_status[1]).toBe('DELIVERED');
    expect(lastLog()).toContain(
      'WARNING: Previous import looks incomplete: 18 of 20 shipments missing. Not treated as duplicates: 2.',
    );
  });

  test('тексты и пороги', () => {
    const pc = JSON.parse(runPythonScript('duplicates_step.py', '', 'texts'));
    const grid = Array.from({ length: 30 }, (_, lost) =>
      Array.from({ length: Math.ceil(200 / 7) }, (_, k) => isIncomplete(lost, k * 7)));
    expect({
      days: DUPLICATE_DAYS, min: INCOMPLETE_MIN, percent: INCOMPLETE_PERCENT,
      alert: duplicatesAlertText(3), incomplete: incompleteReportText(7), grid,
    }).toEqual(pc);
  });
});

describe('версия схемы при импорте', () => {
  const dir = tempDir('ozon-duplicates-schema-');
  afterAll(() => dir.cleanup());

  test('импорт поднимает версию старой базы, неудачный — нет', () => {
    runPythonScript('duplicates_fixtures.py', dir.path);
    const path = join(dir.path, 'base.db');
    const rows = parseReport(readActiveSheet(new Uint8Array(readFileSync(join(dir.path, 'day1.xlsx')))));
    const version = () => withPhone(path, (db) => db.get<{ user_version: number }>('PRAGMA user_version')!.user_version);
    withPhone(path, (db) => db.exec('PRAGMA user_version = 1'));

    const bad = rows.map((r) => ({ ...r, shipment_date_ozon: 'вчера' }));
    expect(() => withPhone(path, (db) => processImport(db, 'bad.xlsx', 'x', bad))).toThrow(ImportError);
    expect(version()).toBe(1);

    withPhone(path, (db) => processImport(db, 'day1.xlsx', 'y', rows));
    expect(version()).toBe(SCHEMA_VERSION);
  });
});
