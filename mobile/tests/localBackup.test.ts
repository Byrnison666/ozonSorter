/**
 * Копии базы на телефоне (src/core/localBackup.ts): ежедневная копия через
 * VACUUM INTO, ротация только после проверенной копии, список копий,
 * восстановление базы с возвратом прежней, если копия не открылась.
 */
import { afterEach, beforeEach, describe, expect, spyOn, test } from 'bun:test';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { addClient } from '../src/core/clients';
import type { Db } from '../src/core/db';
import {
  dailyBackup, inspectBackup, KEEP_DAILY_LOCAL, listBackups, restoreDatabase, restoreQuestion,
} from '../src/core/localBackup';
import { PyDateTime } from '../src/core/py';
import { configureConnection, ensureSchema, SCHEMA_VERSION } from '../src/core/schema';
import type { DbHost } from '../src/core/sync/flow';
import { SyncError, type SyncDeps } from '../src/core/sync/syncService';
import { openBunDb } from './support/bunDb';
import { tempDir } from './support/pc';
import { nodeDeps, sha256Hex } from './support/syncEnv';

const deps: SyncDeps = { ...nodeDeps, clock: { now: () => new PyDateTime(2026, 10, 14, 12, 30, 0) } };

let root: ReturnType<typeof tempDir>;
let dbPath: string;
let dailyDir: string;
let presyncDir: string;
let warn: ReturnType<typeof spyOn>;

/** База, как её создаёт приложение, с клиентами ids. */
function makeDb(path: string, ids: string[], extra?: (db: Db) => void): void {
  const db = openBunDb(path);
  try {
    configureConnection(db);
    ensureSchema(db);
    for (const id of ids) {
      const r = addClient(db, { ozonClientId: id, fullName: `Клиент ${id}`, phone: '', point: 'KOMSOMOLSKAYA_4' });
      if (!r.ok) throw new Error(r.error);
    }
    extra?.(db);
  } finally {
    db.close();
  }
}

function clientIds(path: string): string[] {
  const db = openBunDb(path);
  try {
    return db.all<{ ozon_client_id: string }>('SELECT ozon_client_id FROM clients ORDER BY ozon_client_id')
      .map((r) => r.ozon_client_id);
  } finally {
    db.close();
  }
}

const listing = (dir: string) => (existsSync(dir) ? readdirSync(dir).sort() : []);
const fileSha = (path: string) => sha256Hex(new Uint8Array(readFileSync(path)));

/** Ежедневная копия, как в приложении: из открытой основной базы. */
async function backupNow(): Promise<string | null> {
  const db = openBunDb(dbPath);
  try {
    return await dailyBackup(deps, dbPath, () => db);
  } finally {
    db.close();
  }
}

/** count целых старых копий дня (старше любой новой). */
function oldCopies(count: number): string[] {
  mkdirSync(dailyDir, { recursive: true });
  const names = Array.from({ length: count }, (_, i) =>
    `ozon_sorter_2020-${String(1 + Math.floor(i / 28)).padStart(2, '0')}-${String(1 + (i % 28)).padStart(2, '0')}.db`);
  makeDb(join(dailyDir, names[0]), ['999']);
  for (const name of names.slice(1)) writeFileSync(join(dailyDir, name), readFileSync(join(dailyDir, names[0])));
  return names;
}

beforeEach(() => {
  root = tempDir('ozon-local-backup-');
  dbPath = join(root.path, 'ozon_sorter.db');
  dailyDir = join(root.path, 'backups', 'daily');
  presyncDir = join(root.path, 'backups', 'presync');
  warn = spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  warn.mockRestore();
  root.cleanup();
});

describe('ежедневная копия', () => {
  test('проверенная копия под сегодняшней датой; рабочая база не меняется', async () => {
    makeDb(dbPath, ['111', '222']);
    const sha = fileSha(dbPath);
    const path = await backupNow();
    expect(path).toBe(join(dailyDir, 'ozon_sorter_2026-10-14.db'));
    expect(listing(dailyDir)).toEqual(['ozon_sorter_2026-10-14.db']); // без временных файлов
    expect(clientIds(path!)).toEqual(['111', '222']);
    expect(fileSha(dbPath)).toBe(sha); // синхронизация не увидит правки
    expect(await backupNow()).toBeNull(); // вторая за день не нужна
    expect(listing(dailyDir)).toEqual(['ozon_sorter_2026-10-14.db']);
  });

  test(`хранится ${KEEP_DAILY_LOCAL}, чужие файлы не трогаются`, async () => {
    const old = oldCopies(KEEP_DAILY_LOCAL + 2);
    writeFileSync(join(dailyDir, 'notes.txt'), 'not a backup');
    makeDb(dbPath, ['111']);
    await backupNow();
    const copies = listing(dailyDir).filter((n) => n.endsWith('.db'));
    expect(copies).toEqual([...old.slice(3), 'ozon_sorter_2026-10-14.db']);
    expect(listing(dailyDir)).toContain('notes.txt');
  });

  test('непроверенная копия не публикуется и не вытесняет целые', async () => {
    const old = oldCopies(KEEP_DAILY_LOCAL);
    // Посторонняя таблица: VACUUM INTO копию сделает, проверка её отвергнет.
    makeDb(dbPath, ['111'], (db) => db.exec('CREATE TABLE evil (x)'));
    expect(await backupNow()).toBeNull();
    expect(listing(dailyDir)).toEqual(old);
    expect(warn).toHaveBeenCalled();
  });

  test('испорченная база: копии нет, старые целы', async () => {
    const old = oldCopies(KEEP_DAILY_LOCAL);
    makeDb(dbPath, Array.from({ length: 400 }, (_, i) => `${100000 + i}`));
    const bytes = readFileSync(dbPath);
    bytes.fill(0xff, bytes.length - 3 * 4096 + 16, bytes.length - 2 * 4096 - 16); // страница с данными
    writeFileSync(dbPath, bytes);
    expect(await backupNow()).toBeNull();
    expect(listing(dailyDir)).toEqual(old);
    for (const name of old.slice(0, 3)) expect(clientIds(join(dailyDir, name))).toEqual(['999']);
  });

  test('файл не база: копии нет, старые целы', async () => {
    const old = oldCopies(KEEP_DAILY_LOCAL);
    writeFileSync(dbPath, '<html>not a database</html>'.repeat(200));
    expect(await backupNow()).toBeNull();
    expect(listing(dailyDir)).toEqual(old);
  });

  test('пустую базу не копируем; база закрыта — только предупреждение', async () => {
    makeDb(dbPath, []);
    expect(await backupNow()).toBeNull();
    expect(listing(dailyDir)).toEqual([]);
    makeDb(dbPath, ['111']);
    expect(await dailyBackup(deps, dbPath, () => {
      throw new Error('База сейчас недоступна.');
    })).toBeNull();
    expect(listing(dailyDir)).toEqual([]);
  });

  test('остаток оборванной попытки не мешает', async () => {
    mkdirSync(dailyDir, { recursive: true });
    writeFileSync(join(dailyDir, 'ozon_sorter_2026-10-14.db.tmp'), 'half-written');
    makeDb(dbPath, ['111']);
    expect(await backupNow()).not.toBeNull();
    expect(listing(dailyDir)).toEqual(['ozon_sorter_2026-10-14.db']);
  });
});

describe('список копий', () => {
  test('копии дня и перед заменой, новые первыми, с датой и размером', async () => {
    mkdirSync(dailyDir, { recursive: true });
    mkdirSync(presyncDir, { recursive: true });
    makeDb(join(dailyDir, 'ozon_sorter_2026-10-13.db'), ['111']);
    writeFileSync(join(dailyDir, 'ozon_sorter_2026-10-12.db'), 'x'.repeat(10));
    writeFileSync(join(presyncDir, 'ozon_sorter_20261013_181500_123456.db.bak'), 'y'.repeat(20));
    writeFileSync(join(dailyDir, 'ozon_sorter_2026-10-14.db.tmp'), 'tmp');
    writeFileSync(join(presyncDir, 'notes.txt'), 'not a backup');
    const rows = await listBackups(nodeDeps.files, dbPath);
    expect(rows.map((r) => [r.kind, r.when, r.name])).toEqual([
      ['presync', '13.10.2026 18:15', 'ozon_sorter_20261013_181500_123456.db.bak'],
      ['daily', '13.10.2026', 'ozon_sorter_2026-10-13.db'],
      ['daily', '12.10.2026', 'ozon_sorter_2026-10-12.db'],
    ]);
    expect(rows[0].size).toBe(20);
    expect(rows[0].path).toBe(join(presyncDir, 'ozon_sorter_20261013_181500_123456.db.bak'));
  });

  test('копий нет — пустой список', async () => {
    expect(await listBackups(nodeDeps.files, dbPath)).toEqual([]);
  });
});

/** Основная база приложения: настоящее соединение, как в AppContext. */
class BunHost implements DbHost {
  log: string[] = [];
  db: Db | null = null;
  failReload = 0;

  constructor(private readonly path: string) {}

  release() {
    this.log.push('release');
    this.db?.close();
    this.db = null;
  }

  reload() {
    this.log.push('reload');
    this.db?.close();
    this.db = null;
    if (this.failReload > 0) {
      this.failReload--;
      throw new Error('cannot open');
    }
    const db = openBunDb(this.path);
    configureConnection(db);
    ensureSchema(db);
    this.db = db;
  }
}

describe('восстановление базы', () => {
  let host: BunHost;
  let source: string;

  beforeEach(() => {
    makeDb(dbPath, ['111']);
    host = new BunHost(dbPath);
    host.reload();
    host.log = [];
    source = join(root.path, 'picked.db');
  });
  afterEach(() => host.release());

  test('копия встаёт на место базы, прежняя — в копиях перед заменой', async () => {
    makeDb(source, ['222', '333']);
    const sourceSha = fileSha(source);
    await restoreDatabase(deps, dbPath, source, host);
    expect(host.log).toEqual(['release', 'reload']);
    expect(host.db!.all<{ ozon_client_id: string }>('SELECT ozon_client_id FROM clients').length).toBe(2);
    host.release();
    expect(clientIds(dbPath)).toEqual(['222', '333']);
    const saved = listing(presyncDir);
    expect(saved).toHaveLength(1);
    expect(clientIds(join(presyncDir, saved[0]))).toEqual(['111']);
    expect(fileSha(source)).toBe(sourceSha); // выбранный файл не тронут
    expect(listing(root.path).filter((n) => n.includes('restore-tmp'))).toEqual([]);
  });

  const rejected: Array<[string, () => void, RegExp]> = [
    ['файл не база', () => writeFileSync(source, '<html>not a database</html>'.repeat(200)), /не является базой|не открывается/],
    ['испорченная база', () => {
      makeDb(source, Array.from({ length: 400 }, (_, i) => `${100000 + i}`));
      const bytes = readFileSync(source);
      bytes.fill(0xff, bytes.length - 3 * 4096 + 16, bytes.length - 2 * 4096 - 16);
      writeFileSync(source, bytes);
    }, /повреждён|не является базой/],
    ['пустая база', () => makeDb(source, []), /пустая база/],
    ['база новее приложения', () => makeDb(source, ['222'], (db) => db.exec(`PRAGMA user_version = ${SCHEMA_VERSION + 1}`)),
      /более новой версией/],
    ['не база программы', () => {
      const db = openBunDb(source);
      db.exec("CREATE TABLE clients (id INTEGER PRIMARY KEY, ozon_client_id VARCHAR); INSERT INTO clients VALUES (1, '1')");
      db.close();
    }, /нет таблиц приложения/],
    ['таблицы без колонок приложения', () => makeDb(source, ['222'], (db) => db.exec('DROP INDEX idx_clients_is_active; ALTER TABLE clients DROP COLUMN is_active')),
      /не хватает колонок/],
    ['старая версия без колонок приложения', () => makeDb(source, ['222'], (db) => db.exec(
      'PRAGMA user_version = 1; ALTER TABLE import_sessions DROP COLUMN returned_rows')),
      /не хватает колонок/],
    ['копия ПК до версии 1.8.0', () => makeDb(source, ['222'], (db) => db.exec('PRAGMA user_version = 0')),
      /до версии 1\.8\.0/],
  ];
  for (const [name, make, message] of rejected) {
    test(`${name}: отказ до замены, база открыта и не тронута`, async () => {
      make();
      const sha = fileSha(dbPath);
      const error = await restoreDatabase(deps, dbPath, source, host).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(SyncError);
      expect((error as Error).message).toMatch(message);
      expect(host.log).toEqual([]); // база не закрывалась
      expect(host.db).not.toBeNull();
      expect(fileSha(dbPath)).toBe(sha);
      expect(listing(presyncDir)).toEqual([]);
      expect(listing(root.path).filter((n) => n.includes('restore-tmp'))).toEqual([]);
    });
  }

  test('копия ПК 1.8.0–1.10.x (схема 1) восстанавливается', async () => {
    makeDb(source, ['222'], (db) => db.exec('PRAGMA user_version = 1'));
    await restoreDatabase(deps, dbPath, source, host);
    host.release();
    expect(clientIds(dbPath)).toEqual(['222']);
  });

  test('копия не открылась в приложении — возвращена прежняя база', async () => {
    makeDb(source, ['222']);
    host.failReload = 1;
    const error = await restoreDatabase(deps, dbPath, source, host).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SyncError);
    expect((error as Error).message).toContain('возвращена прежняя база');
    expect(host.log).toEqual(['release', 'reload', 'release', 'reload']);
    expect(host.db).not.toBeNull(); // прежняя снова открыта
    host.release();
    expect(clientIds(dbPath)).toEqual(['111']);
    expect(listing(root.path).filter((n) => n.includes('restore-tmp'))).toEqual([]);
  });

  test('подтверждение называет копию, её содержимое и последствия для Диска', async () => {
    makeDb(source, ['222', '333'], (db) => db.run(
      `INSERT INTO import_sessions (source_file_name, source_file_sha256, started_at, total_rows, kty_rows,
        matched_rows, new_to_ship_rows, already_on_point, returned_rows, not_ours_rows, errors_rows)
       VALUES ('r.xlsx', 'x', '2026-10-13 18:20:05.000001', 0, 0, 0, 0, 0, 0, 0, 0)`,
    ));
    const summary = inspectBackup(deps, source);
    expect(summary).toEqual({ clients: 2, lastImport: '2026-10-13 18:20:05.000001' });
    const text = restoreQuestion('копии от 13.10.2026', summary);
    expect(text).toContain('копии от 13.10.2026');
    expect(text).toContain('клиентов 2, последний импорт 13.10.2026 18:20');
    expect(text).toContain('заменит и базу на Яндекс.Диске');
    expect(text).toContain('сохранится в резервных копиях');
  });
});
