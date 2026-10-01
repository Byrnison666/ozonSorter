import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';

import type { DeliveryPoint } from '../src/core/models';
import { configureConnection, ensureSchema } from '../src/core/schema';
import {
  exportStaleXlsx, firstSeenLabel, listStale, staleCells, type StaleRow,
} from '../src/core/staleService';
import { openBunDb } from './support/bunDb';
import { runPythonScript, tempDir } from './support/pc';

function phoneView(r: StaleRow) {
  return {
    days: r.days, cell: r.cell, posting_number: r.posting_number,
    product_label: r.product_label, product_name: r.product_name,
    client_name: r.client_name, ozon_client_id: r.ozon_client_id, phone: r.phone,
    point: r.point, first_seen_label: firstSeenLabel(r), is_returned: r.is_returned,
    cells: staleCells(r),
  };
}

describe('залежавшиеся совпадают с ПК', () => {
  const dir = tempDir('ozon-stale-');
  const db = () => join(dir.path, 'base.db');
  const describeXlsx = (p: string) => JSON.parse(runPythonScript('xlsx_describe.py', p));

  beforeAll(() => runPythonScript('export_fixtures.py', dir.path));
  afterAll(() => dir.cleanup());

  const cases: Array<[string, DeliveryPoint | '', number]> = [
    ['все', '', 0], ['Комсомольская', 'KOMSOMOLSKAYA_4', 0], ['Кольцевая', 'KOLTSEVAYA_16', 0],
    ['от 3 дней', '', 3], ['от 7 дней', '', 7], ['никого', 'KOMSOMOLSKAYA_4', 100],
  ];

  test.each(cases)('%s', (name, point, minDays) => {
    const pcOut = join(dir.path, `pc-${name}.xlsx`);
    const phoneOut = join(dir.path, `phone-${name}.xlsx`);
    const pc = JSON.parse(runPythonScript('stale_step.py', db(), point, String(minDays), pcOut));
    const conn = openBunDb(db());
    configureConnection(conn);
    const rows = listStale(conn, point || null, minDays);
    conn.close();
    writeFileSync(phoneOut, exportStaleXlsx(rows));
    expect(rows.map(phoneView)).toEqual(pc);
    expect(describeXlsx(phoneOut)).toEqual(describeXlsx(pcOut));
  });

  test('эталон содержательный: подсветка, возврат, ноль дней', () => {
    const pc = JSON.parse(runPythonScript('stale_step.py', db(), '', '0', join(dir.path, 'x.xlsx')));
    const days = pc.map((r: { days: number }) => r.days);
    expect(Math.max(...days)).toBeGreaterThanOrEqual(7);
    expect(days).toContain(0);
    expect(pc.some((r: { is_returned: boolean }) => r.is_returned)).toBe(true);
  });

  test('без импортов — пусто', () => {
    const empty = openBunDb(join(dir.path, 'empty.db'));
    configureConnection(empty);
    ensureSchema(empty);
    expect(listStale(empty)).toEqual([]);
    empty.close();
  });
});
