import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { checkReport, parseReport, type ParsedRow } from '../src/core/parser';
import { type CellValue, PyDateTime, PyTime, pyStr } from '../src/core/py';
import { readActiveSheet } from '../src/core/sheet';
import { runPythonScript, tempDir } from './support/pc';

type Parsed = {
  rows?: Record<string, unknown>[];
  check?: { errors: string[]; warnings: string[] };
  error?: string;
};
type Golden = Record<string, Parsed>;

/** Та же кодировка, что encode() в tests/py/parser_golden.py. */
function encode(value: CellValue | undefined): unknown {
  if (value instanceof PyDateTime) return { dt: value.toString() };
  if (value instanceof PyTime) return { time: value.toString() };
  if (typeof value === 'number' && !Number.isInteger(value)) return { float: pyStr(value) };
  return value ?? null;
}

function parseFile(path: string): Parsed {
  try {
    const rows = parseReport(readActiveSheet(new Uint8Array(readFileSync(path))));
    return {
      rows: rows.map((r: ParsedRow) =>
        Object.fromEntries(Object.entries(r).map(([k, v]) => [k, encode(v as CellValue)])),
      ),
      check: checkReport(rows),
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
    'header_variants', 'header_too_low', 'epoch_1904', 'time_only_date', 'stale_dimension',
    'unknown_status', 'empty_under_header', 'mostly_kty', 'excel_resaved',
  ];

  test.each(cases)('%s', (name) => {
    expect(golden[name]).toBeDefined();
    expect(parseFile(join(dir.path, `${name}.xlsx`))).toEqual(golden[name]);
  });

  test('настоящий отчёт разобран целиком', () => {
    expect(golden.excel_resaved.rows).toHaveLength(1294);
  });

  test('отчёт с устаревшим размером листа разобран целиком', () => {
    expect(golden.stale_dimension.rows).toHaveLength(3);
    expect(golden.stale_dimension.check).toEqual({ errors: [], warnings: [] });
  });

  test('проверка отчёта: в эталоне есть ошибки и предупреждения', () => {
    expect(golden.unknown_status.check?.warnings).toHaveLength(1);
    expect(golden.empty_under_header.check?.errors).toHaveLength(1);
    expect(golden.mostly_kty.check?.errors).toHaveLength(1);
  });

  test('эталон покрывает все случаи скрипта', () => {
    expect(Object.keys(golden).sort()).toEqual([...cases].sort());
  });
});
