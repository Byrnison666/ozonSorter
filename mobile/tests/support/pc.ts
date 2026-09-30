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
    env: { ...process.env, QT_QPA_PLATFORM: 'offscreen' },
  });
  if (proc.exitCode !== 0) {
    throw new Error(`python failed (${proc.exitCode}):\n${proc.stderr.toString()}`);
  }
  return proc.stdout.toString();
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
