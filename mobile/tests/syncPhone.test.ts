/**
 * Синхронизация на телефоне: состояния, отказы и недоверенное содержимое Диска —
 * перенос ключевых тестов tests/test_sync_service.py ПК.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'fflate';

import type { Db } from '../src/core/db';
import { PyDateTime } from '../src/core/py';
import { configureConnection, ensureSchema, SCHEMA_VERSION } from '../src/core/schema';
import {
  CancelToken, parseMeta, SyncCancelled, SyncError, type SyncDeps,
} from '../src/core/sync/syncService';
import { WebDavError } from '../src/core/sync/webdav';
import { openBunDb } from './support/bunDb';
import { tempDir } from './support/pc';
import {
  type DavServer, META_PATH, nodeDeps, Phone, REMOTE_DIR, sha256Hex, startDavServer,
} from './support/syncEnv';

let server: DavServer;
let root: ReturnType<typeof tempDir>;
let a: Phone;
let b: Phone;
let n = 0;

function freshDir(name: string): string {
  const dir = join(root.path, `${name}-${++n}`);
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** Байты настоящей базы приложения; mutate может её испортить. */
function dbBytes(mutate?: (db: Db) => void): Uint8Array {
  const path = join(freshDir('bytes'), 'x.db');
  const db = openBunDb(path);
  configureConnection(db);
  ensureSchema(db);
  mutate?.(db);
  db.close();
  return new Uint8Array(readFileSync(path));
}

/** Выложить на «Диск» что угодно в обход клиентов. */
async function publish(
  raw: Uint8Array,
  { rev = 1, schemaVersion = SCHEMA_VERSION, packed, ...overrides }:
  { rev?: number; schemaVersion?: number; packed?: Uint8Array } & Record<string, unknown> = {},
) {
  const sha = sha256Hex(raw);
  const name = `ozon_sorter_rev${String(rev).padStart(6, '0')}_${sha.slice(0, 8)}.db.gz`;
  await server.put(`/${REMOTE_DIR}/${name}`, packed ?? gzipSync(raw));
  const meta = {
    rev, file: name, sha256: sha, schema_version: schemaVersion,
    device: 'чужой', saved_at: '2026-09-30 12:00:00', ...overrides,
  };
  await server.put(META_PATH, JSON.stringify(meta));
  return meta;
}

beforeAll(async () => {
  server = await startDavServer();
  root = tempDir('ozon-sync-phone-');
});
afterAll(async () => {
  await server.stop();
  root.cleanup();
});
beforeEach(async () => {
  await server.control({ cmd: 'clear' });
  await server.control({ cmd: 'set', offline: false, truncate_gets: false });
  a = new Phone(freshDir('a'), server);
  b = new Phone(freshDir('b'), server);
  a.open(() => undefined);
  b.open(() => undefined);
});

async function seed() {
  a.addClient('111');
  await a.push();
  await b.pull();
}

describe('состояния', () => {
  test('пусто здесь и на Диске — совпадает; данные только здесь — выложить', async () => {
    expect(await a.status()).toBe('UP_TO_DATE');
    a.addClient('111');
    expect(await a.status()).toBe('LOCAL_AHEAD');
  });

  test('новое устройство с пустой базой скачивает, а не конфликтует', async () => {
    a.addClient('111');
    await a.push();
    expect(await b.status()).toBe('REMOTE_AHEAD');
  });

  test('потеря файла состояния при совпадающем содержимом — совпадает', async () => {
    a.addClient('111');
    await a.push();
    rmSync(join(a.dir, 'sync_state.json'));
    expect(await a.status()).toBe('UP_TO_DATE');
    expect(await a.sync.localChanged()).toBe(false); // состояние восстановлено
  });

  test('«Диск пропал» — только у устройства с данными, уже синхронизировавшегося', async () => {
    await seed();
    await server.control({ cmd: 'clear' });
    expect(await b.status()).toBe('REMOTE_MISSING');
    const c = new Phone(freshDir('c'), server);
    c.addClient('5');
    expect(await c.status()).toBe('LOCAL_AHEAD');
  });

  test('старая ревизия на Диске помечается', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    await b.pull(); // B видел rev2
    const rev1 = await publish(dbBytes(), { rev: 1 });
    expect(rev1.rev).toBe(1);
    const check = await b.sync.check();
    expect(check.status).toBe('REMOTE_AHEAD');
    expect(check.remoteIsOlder).toBe(true);
  });

  test('та же ревизия, заменённая другой базой, помечается', async () => {
    await seed();
    a.addClient('222');
    await a.push(); // rev2
    await b.pull();
    await publish(dbBytes((db) => db.exec("INSERT INTO clients (ozon_client_id, is_active, created_at, updated_at) VALUES ('777', 1, 'x', 'x')")), { rev: 2 });
    const check = await b.sync.check();
    expect(check.status).toBe('REMOTE_AHEAD');
    expect(check.remoteIsOlder).toBe(true);
  });

  test('номер ревизии продолжается от наибольшего виденного', async () => {
    await seed();
    a.addClient('222');
    await a.push(); // rev2
    await b.pull();
    await publish(dbBytes((db) => db.exec("INSERT INTO clients (ozon_client_id, is_active, created_at, updated_at) VALUES ('9', 1, 'x', 'x')")), { rev: 1 });
    b.addClient('333');
    const check = await b.sync.check();
    expect(check.status).toBe('CONFLICT');
    expect(await b.sync.push(check.meta)).toBe(3);
  });

  test('запись времени синхронизации и откат загрузки', async () => {
    await seed();
    const state = JSON.parse(readFileSync(join(b.dir, 'sync_state.json'), 'utf-8'));
    expect(state.synced_at).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/);
    state.synced_at = '2026-09-29 08:15:00';
    writeFileSync(join(b.dir, 'sync_state.json'), JSON.stringify(state));
    a.addClient('222');
    await a.push();
    await b.pull();
    expect(b.clientIds()).toEqual(['111', '222']);
    await b.sync.rollbackPull();
    expect(b.clientIds()).toEqual(['111']);
    const restored = JSON.parse(readFileSync(join(b.dir, 'sync_state.json'), 'utf-8'));
    expect(restored.synced_at).toBe('2026-09-29 08:15:00');
    expect(await b.sync.localChanged()).toBe(false);
  });
});

describe('обмен и отказы', () => {
  test('выгрузка отказывает, если Диск изменился после проверки', async () => {
    await seed();
    const check = await b.sync.check();
    a.addClient('222');
    await a.push();
    b.addClient('333');
    await expect(b.sync.push(check.meta)).rejects.toBeInstanceOf(SyncError);
  });

  test('загрузка отказывает, если Диск изменился после согласия', async () => {
    a.addClient('111');
    await a.push();
    const check = await b.sync.check();
    a.addClient('222');
    await a.push();
    await expect(b.sync.pull(check.meta)).rejects.toThrow('только что изменилась');
  });

  test('оставшийся журнал блокирует синхронизацию', async () => {
    a.addClient('111');
    writeFileSync(`${a.dbPath}-journal`, 'x');
    await expect(a.sync.check()).rejects.toThrow('незавершённой записью');
  });

  test('копия заменённой базы — в backups/presync, их не больше 10', async () => {
    await seed();
    for (let i = 0; i < 12; i++) {
      a.addClient(String(500 + i));
      await a.push();
      await b.pull();
    }
    const dir = join(b.dir, 'backups', 'presync');
    expect(readdirSync(dir).length).toBe(10);
  });

  test('отмена до необратимого шага: база и ревизия не меняются', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    const token = new CancelToken();
    expect(token.cancel()).toBe(true);
    await expect(b.sync.pull((await b.sync.check()).meta, token)).rejects.toBeInstanceOf(SyncCancelled);
    expect(b.clientIds()).toEqual(['111']);

    b.addClient('999');
    const cancelled = new CancelToken();
    cancelled.cancel();
    const metaBefore = await server.get(META_PATH);
    await expect(b.sync.push((await b.sync.check()).meta, cancelled)).rejects.toBeInstanceOf(SyncCancelled);
    expect(await server.get(META_PATH)).toEqual(metaBefore);
  });

  test('после необратимого шага отмена отклоняется', () => {
    const token = new CancelToken();
    token.commit();
    expect(token.cancel()).toBe(false);
  });

  test('сервер недоступен — сетевая ошибка', async () => {
    await server.control({ cmd: 'set', offline: true });
    const err = await a.sync.check().catch((e) => e);
    expect(err).toBeInstanceOf(WebDavError);
    expect(err.isNetworkError).toBe(true);
  });

  test('обрыв ответа — сетевая ошибка, а не «файл повреждён»', async () => {
    a.addClient('111');
    await a.push();
    await server.control({ cmd: 'set', truncate_gets: true });
    const err = await b.sync.check().catch((e) => e);
    expect(err).toBeInstanceOf(WebDavError);
    expect(err.isNetworkError).toBe(true);
  });

  test('сервер не принял пароль — ошибка авторизации', async () => {
    await server.control({ cmd: 'fail_next', method: 'GET', code: 401 });
    const err = await a.sync.check().catch((e) => e);
    expect(err).toBeInstanceOf(WebDavError);
    expect(err.isAuthError).toBe(true);
  });

  test('повреждённую локальную базу не выкладываем', async () => {
    writeFileSync(a.dbPath, Buffer.concat([Buffer.from('SQLite format 3\0'), Buffer.alloc(4096, 7)]));
    await expect(a.sync.push(null)).rejects.toBeInstanceOf(SyncError);
    expect(await server.get(META_PATH)).toBeNull();
  });

  test('локальную базу с триггером не выкладываем', async () => {
    a.open((db) => db.exec('CREATE TRIGGER t AFTER INSERT ON clients BEGIN SELECT 1; END'));
    a.addClient('1');
    await expect(a.sync.push(null)).rejects.toThrow('посторонние объекты');
  });
});

describe('недоверенное содержимое Диска', () => {
  async function assertBUntouched() {
    expect(b.clientIds()).toEqual(['999']);
    expect(existsSync(`${b.dbPath}.sync-tmp`)).toBe(false);
    expect(existsSync(join(b.dir, 'backups', 'presync'))).toBe(false);
  }

  beforeEach(() => b.addClient('999'));

  const rejects = async (fn: () => Promise<unknown>, cls: unknown = SyncError) => {
    await expect(fn()).rejects.toBeInstanceOf(cls as never);
    await assertBUntouched();
  };

  test('не SQLite с верной суммой', () => rejects(async () => {
    await publish(new TextEncoder().encode('this is not sqlite at all '.repeat(40)));
    await b.pull();
  }));

  test('мусор после заголовка SQLite', () => rejects(async () => {
    const junk = new Uint8Array(8192 + 16);
    junk.set(new TextEncoder().encode('SQLite format 3\0'));
    crypto.getRandomValues(junk.subarray(16));
    await publish(junk);
    await b.pull();
  }));

  test.each([
    ['триггер', "CREATE TRIGGER pwn AFTER INSERT ON clients BEGIN UPDATE clients SET full_name = 'pwned'; END"],
    ['представление', 'CREATE VIEW v AS SELECT * FROM clients'],
    ['посторонняя таблица', 'CREATE TABLE evil (x)'],
  ])('база с объектом: %s', (_, sql) => rejects(async () => {
    await publish(dbBytes((db) => db.exec(sql)));
    await b.pull();
  }));

  test('версия схемы берётся из файла, а не только из meta', () => rejects(async () => {
    await publish(dbBytes((db) => db.exec(`PRAGMA user_version = ${SCHEMA_VERSION + 50}`)));
    await b.pull();
  }));

  test('более новая схема в meta — отказ до скачивания', async () => {
    await publish(dbBytes(), { schemaVersion: SCHEMA_VERSION + 1 });
    await expect(b.pull()).rejects.toThrow('Обновите приложение');
    await assertBUntouched();
  });

  test('сумма не совпадает', () => rejects(async () => {
    const meta = await publish(dbBytes());
    await server.put(`/${REMOTE_DIR}/${meta.file}`, gzipSync(dbBytes((db) => db.exec('PRAGMA user_version = 1'))));
    await b.pull();
  }));

  test('не gzip', () => rejects(async () => {
    await publish(dbBytes(), { packed: new Uint8Array([0, 1, 32, 110, 111, 116]) });
    await b.pull();
  }));

  test('битый deflate-поток', () => rejects(async () => {
    const broken = new Uint8Array([0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0, ...new Array(64).fill(0xff)]);
    await publish(dbBytes(), { packed: broken });
    await b.pull();
  }));

  test('gzip-бомба', async () => {
    const small = new Phone(b.dir, server, { maxDbBytes: 10_000 });
    await publish(new Uint8Array(100_000));
    await expect(small.pull()).rejects.toThrow('слишком большой');
    await assertBUntouched();
  });

  test('слишком большой meta.json', async () => {
    await publish(dbBytes());
    await server.put(META_PATH, ' '.repeat(70 * 1024));
    await expect(b.sync.remoteMeta()).rejects.toBeInstanceOf(WebDavError);
  });

  test('meta.json не JSON', () => rejects(async () => {
    await publish(dbBytes());
    await server.put(META_PATH, '{"rev": 1,');
    await b.pull();
  }));

  test('недопустимые значения в meta.json', async () => {
    const sha = sha256Hex(dbBytes());
    const cases: Record<string, Record<string, unknown>> = {
      'rev zero': { rev: 0 },
      'rev negative': { rev: -5 },
      'rev too large': { rev: 999_999 },
      'rev bool': { rev: true },
      'rev float': { rev: 1.5 },
      'rev string': { rev: '1' },
      'schema bool': { schema_version: true },
      'schema negative': { schema_version: -1 },
      'sha upper case': { sha256: sha.toUpperCase() },
      'sha short': { sha256: sha.slice(0, 40) },
      'file of another revision': { file: `ozon_sorter_rev000007_${sha.slice(0, 8)}.db.gz` },
      'file of another content': { file: 'ozon_sorter_rev000001_deadbeef.db.gz' },
      'file with path': { file: `../ozon_sorter_rev000001_${sha.slice(0, 8)}.db.gz` },
      'file with trailing newline': { file: `ozon_sorter_rev000001_${sha.slice(0, 8)}.db.gz\n` },
      'file with unicode digits': { file: `ozon_sorter_rev００００１_${sha.slice(0, 8)}.db.gz` },
      'file not a string': { file: 5 },
    };
    const good = { rev: 1, file: `ozon_sorter_rev000001_${sha.slice(0, 8)}.db.gz`, sha256: sha, schema_version: 1 };
    expect(() => parseMeta(new TextEncoder().encode(JSON.stringify(good)))).not.toThrow();
    for (const [label, change] of Object.entries(cases)) {
      const raw = new TextEncoder().encode(JSON.stringify({ ...good, ...change }));
      expect(() => parseMeta(raw), label).toThrow();
    }
  });

  test('текст из meta очищается для диалогов и ограничен по длине', () => {
    const sha = sha256Hex(dbBytes());
    const base = { rev: 1, file: `ozon_sorter_rev000001_${sha.slice(0, 8)}.db.gz`, sha256: sha, schema_version: 1 };
    const meta = parseMeta(new TextEncoder().encode(JSON.stringify({
      ...base, device: '<img src=x>\u0007Тел\u202eефон&' + 'я'.repeat(100), saved_at: '2026-13-01 00:00:00',
    })));
    expect(meta.device).toBe('img src=xТелефон' + 'я'.repeat(48));
    expect(meta.saved_at).toBe('');
    const ok = parseMeta(new TextEncoder().encode(JSON.stringify({ ...base, saved_at: '2026-09-30 18:00:00' })));
    expect(ok.saved_at).toBe('2026-09-30 18:00:00');
  });
});

describe('зависимости платформы', () => {
  test('время берётся из часов, переданных снаружи', async () => {
    const fixed: SyncDeps = { ...nodeDeps, clock: { now: () => new PyDateTime(2026, 9, 30, 7, 8, 9) } };
    const p = new Phone(freshDir('clock'), server, {}, fixed);
    p.addClient('1');
    await p.push();
    const meta = JSON.parse(new TextDecoder().decode((await server.get(META_PATH))!));
    expect(meta.saved_at).toBe('2026-09-30 07:08:09');
  });
});
