/**
 * Эталон — код ПК. Тесты запускают Python-исходники из корня репозитория
 * (venv ../.venv), чтобы сравнивать поведение телефона с настоящим, а не с копией.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

export const REPO_ROOT = resolve(import.meta.dir, '../../..');
const PYTHON = join(REPO_ROOT, '.venv/bin/python');

/** Выполнить Python-код в корне репозитория; вернуть stdout. argv — sys.argv[1:]. */
export function runPython(code: string, ...argv: string[]): string {
  const proc = Bun.spawnSync([PYTHON, '-c', code, ...argv], {
    cwd: REPO_ROOT,
    // bun test сам работает в UTC; Python должен считать местное время так же.
    env: {
      ...process.env,
      QT_QPA_PLATFORM: 'offscreen',
      // libxcb-xinerama для PySide2 (см. CLAUDE.md репозитория).
      LD_LIBRARY_PATH: [join(process.env.HOME ?? '', '.local/qtlibs'), process.env.LD_LIBRARY_PATH]
        .filter(Boolean).join(':'),
      TZ: Intl.DateTimeFormat().resolvedOptions().timeZone,
    },
  });
  if (proc.exitCode !== 0) {
    throw new Error(`python failed (${proc.exitCode}):\n${proc.stderr.toString()}`);
  }
  return proc.stdout.toString();
}

/** Запустить скрипт из tests/py (импорты src.* — из корня репозитория). */
export function runPythonScript(name: string, ...argv: string[]): string {
  return runPython(
    'import runpy, sys\n' +
      "sys.argv = sys.argv[1:]\n" +
      "runpy.run_path(sys.argv[0], run_name='__main__')\n",
    join(import.meta.dir, '../py', name),
    ...argv,
  );
}

/** Создать базу так, как её создаёт программа на ПК при первом запуске. */
export function createPcDatabase(path: string): void {
  runPython(
    'import sys\n' +
      'from src.database import DatabaseManager\n' +
      'db = DatabaseManager(db_path=sys.argv[1])\n' +
      'db.create_tables()\n' +
      'db.engine.dispose()\n',
    path,
  );
}

export function tempDir(prefix: string): { path: string; cleanup: () => void } {
  const path = mkdtempSync(join(tmpdir(), prefix));
  return { path, cleanup: () => rmSync(path, { recursive: true, force: true }) };
}
