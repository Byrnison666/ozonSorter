import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { parseReport, type ParsedRow } from '../src/core/parser';
import { type CellValue, PyDateTime, PyTime, pyStr } from '../src/core/py';
import { readActiveSheet } from '../src/core/sheet';
import { runPythonScript, tempDir } from './support/pc';

type Golden = Record<string, { rows?: Record<string, unknown>[]; error?: string }>;

/** Та же кодировка, что encode() в tests/py/parser_golden.py. */
function encode(value: CellValue | undefined): unknown {
  if (value instanceof PyDateTime) return { dt: value.toString() };
  if (value instanceof PyTime) return { time: value.toString() };
  if (typeof value === 'number' && !Number.isInteger(value)) return { float: pyStr(value) };
  return value ?? null;
}

function parseFile(path: string): { rows?: Record<string, unknown>[]; error?: string } {
  try {
    const rows = parseReport(readActiveSheet(new Uint8Array(readFileSync(path))));
    return {
      rows: rows.map((r: ParsedRow) =>
        Object.fromEntries(Object.entries(r).map(([k, v]) => [k, encode(v as CellValue)])),
      ),
    };
  } catch (e) {
    return { error: (e as Error).message };
  }
}

describe('разбор отчёта совпадает с ПК', () => {
  const dir = tempDir('ozon-parser-');
  let golden: Golden;

  beforeAll(() => {
    golden = JSON.parse(runPythonScript('parser_golden.py', dir.path));
  });
  afterAll(() => dir.cleanup());

  const cases = [
    'new_format', 'old_format', 'active_second_sheet', 'no_header',
    'header_variants', 'header_too_low', 'epoch_1904', 'time_only_date', 'excel_resaved',
  ];

  test.each(cases)('%s', (name) => {
    expect(golden[name]).toBeDefined();
    expect(parseFile(join(dir.path, `${name}.xlsx`))).toEqual(golden[name]);
  });

  test('настоящий отчёт разобран целиком', () => {
    expect(golden.excel_resaved.rows).toHaveLength(1294);
  });

  test('эталон покрывает все случаи скрипта', () => {
    expect(Object.keys(golden).sort()).toEqual([...cases].sort());
  });
});
