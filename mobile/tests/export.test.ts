import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { copyFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { writeClientTemplate } from '../src/core/clientImport';
import type { Db } from '../src/core/db';
import { commitExport, ExportError, prepareExport } from '../src/core/exportService';
import type { DeliveryPoint } from '../src/core/models';
import { configureConnection, ensureSchema } from '../src/core/schema';
import { writeXlsx } from '../src/core/xlsxWriter';
import { openBunDb } from './support/bunDb';
import { dumpDb, StepClock } from './support/dump';
import { runPython, runPythonScript, tempDir } from './support/pc';

describe('выгрузка совпадает с ПК', () => {
  const dir = tempDir('ozon-export-');
  const pcDb = join(dir.path, 'pc.db');
  const phoneDb = join(dir.path, 'phone.db');
  const clock = new StepClock();
  const dump = (p: string) => dumpDb(p, clock);
  const describeXlsx = (p: string) => JSON.parse(runPythonScript('xlsx_describe.py', p));

  function withPhone<T>(fn: (db: Db) => T): T {
    const db = openBunDb(phoneDb);
    configureConnection(db);
    ensureSchema(db);
    try {
      return fn(db);
    } finally {
      db.close();
    }
  }

  beforeAll(() => {
    runPythonScript('export_fixtures.py', dir.path);
    copyFileSync(join(dir.path, 'base.db'), pcDb);
    copyFileSync(join(dir.path, 'base.db'), phoneDb);
  });
  afterAll(() => dir.cleanup());

  const steps: Array<[string, DeliveryPoint, number]> = [
    ['k4-s2', 'KOMSOMOLSKAYA_4', 2],
    ['k4-s2-repeat', 'KOMSOMOLSKAYA_4', 2],
    ['k16-s2', 'KOLTSEVAYA_16', 2],
    ['k4-s1', 'KOMSOMOLSKAYA_4', 1],
  ];

  test.each(steps)('%s', (step, point, sessionId) => {
    // Один и тот же путь в export_sessions.file_path у обоих.
    const out = join(dir.path, `${step}.xlsx`);
    const phoneOut = join(dir.path, `${step}-phone.xlsx`);
    clock.run(step, () => {
      const pc = JSON.parse(runPythonScript('export_step.py', pcDb, point, String(sessionId), out));
      const phone = withPhone((db) => {
        const prepared = prepareExport(db, point, sessionId);
        writeFileSync(phoneOut, prepared.bytes);
        return { export_id: commitExport(db, prepared, out) };
      });
      expect(phone).toEqual(pc);
    });
    const pcFile = describeXlsx(out);
    expect(describeXlsx(phoneOut)).toEqual(pcFile);
    expect(pcFile.cells.length).toBeGreaterThan(0);
    expect(dump(phoneDb)).toEqual(dump(pcDb));
  });

  test('несуществующая сессия: оба отказывают', () => {
    const pc = JSON.parse(runPythonScript(
      'export_step.py', pcDb, 'KOMSOMOLSKAYA_4', '99', join(dir.path, 'none.xlsx'),
    ));
    let phone = '';
    try {
      withPhone((db) => prepareExport(db, 'KOMSOMOLSKAYA_4', 99));
    } catch (e) {
      expect(e).toBeInstanceOf(ExportError);
      phone = (e as Error).message;
    }
    expect(phone).toBe(pc.error);
  });

  test('эталон содержательный: сортировка, повреждения, отметки', () => {
    const cells = describeXlsx(join(dir.path, 'k4-s2.xlsx')).cells as Array<{ ref: string; value: unknown; fill: unknown }>;
    const column = (c: string) => cells.filter((x) => x.ref.startsWith(c) && /^[A-Z]\d+$/.test(x.ref));
    expect(column('D').map((x) => x.value).slice(0, 4)).toEqual([null, null, '1-1', '2-1']);
    expect(cells.some((x) => x.value === 'ПОВРЕЖДЕНО' && x.fill !== null)).toBe(true);
  });

  test('шаблон клиентов совпадает с ПК', () => {
    const pcPath = join(dir.path, 'template-pc.xlsx');
    const phonePath = join(dir.path, 'template-phone.xlsx');
    runPython(
      'import sys\nfrom src.client_import_service import ClientImportService\n' +
        'ClientImportService.write_template(sys.argv[1])\n',
      pcPath,
    );
    writeFileSync(phonePath, writeClientTemplate());
    expect(describeXlsx(phonePath)).toEqual(describeXlsx(pcPath));
  });

  test('запрещённые в XML символы выбрасываются, остальное доходит до openpyxl как есть', () => {
    const path = join(dir.path, 'escape.xlsx');
    const tricky = 'a\x01b\x1f_x0041_<&>" я';
    writeFileSync(path, writeXlsx({ name: 'Лист <1>', rows: [[{ value: tricky }]] }));
    const d = describeXlsx(path);
    expect(d.title).toBe('Лист <1>');
    expect(d.cells[0].value).toBe('ab_x0041_<&>"\u00a0я');
  });
});
