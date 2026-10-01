/**
 * Пустая база телефона (новая установка, переустановка, стёртые данные) не
 * должна затереть данные ПК на Диске ни одним путём: ручная синхронизация,
 * запуск приложения, уход в фон, прямой вызов выгрузки.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  backgroundPush, type DbHost, startupAction, syncInteractive, type SyncUi,
} from '../src/core/sync/flow';
import { CancelToken } from '../src/core/sync/syncService';
import { tempDir } from './support/pc';
import { type DavServer, META_PATH, Pc, Phone, startDavServer } from './support/syncEnv';

let server: DavServer;
let root: ReturnType<typeof tempDir>;
let pc: Pc;
let n = 0;

const host: DbHost = { release() {}, reload() {} };
const online = async () => true;

/** Интерфейс, который на любой вопрос отвечает так, как просят. */
function ui(answers: { pull?: boolean; conflict?: 'local' | 'remote' | ''; emptyDisk?: boolean } = {}) {
  const asked: string[] = [];
  const impl: SyncUi = {
    withProgress: (_t, fn) => fn(new CancelToken()),
    askConflict: async () => {
      asked.push('conflict');
      return answers.conflict ?? '';
    },
    askPull: async () => {
      asked.push('pull');
      return answers.pull ?? false;
    },
    askPushToEmptyDisk: async () => {
      asked.push('emptyDisk');
      return answers.emptyDisk ?? false;
    },
    askRetryOffline: async () => false,
    showMessage: async () => undefined,
  };
  return { impl, asked };
}

type Variant = [string, (p: Phone) => void];
const variants: Variant[] = [
  ['файла базы нет (новая установка)', () => undefined],
  ['пустая база со схемой (приложение запускали)', (p) => p.open(() => undefined)],
  ['файл базы нулевой длины', (p) => writeFileSync(p.dbPath, '')],
  ['пустая база и потерянное состояние после прошлых обменов', (p) => {
    p.open(() => undefined);
    writeFileSync(join(p.dir, 'sync_state.json'), JSON.stringify({ base_rev: 1, base_sha256: 'x'.repeat(64), target: 't1' }));
  }],
];

function freshPhone(): Phone {
  const dir = join(root.path, `phone-${++n}`);
  mkdirSync(dir, { recursive: true });
  return new Phone(dir, server);
}

async function diskMeta(): Promise<string> {
  return new TextDecoder().decode((await server.get(META_PATH))!);
}

beforeAll(async () => {
  server = await startDavServer();
  root = tempDir('ozon-empty-');
});
afterAll(async () => {
  await server.stop();
  root.cleanup();
});
beforeEach(async () => {
  await server.control({ cmd: 'clear' });
  pc = new Pc(join(root.path, `pc-${++n}`), server);
  pc.run('init');
  for (const id of ['111', '222', '333']) pc.run('add_client', id);
  pc.run('push');
});

describe.each(variants)('пустой телефон: %s', (_name, prepare) => {
  test('статус — «скачать с Диска», а не «выложить»', async () => {
    const phone = freshPhone();
    prepare(phone);
    expect(await phone.sync.localChanged()).toBe(false);
    expect(await phone.status()).toBe('REMOTE_AHEAD');
  });

  test('ручная «Синхронизировать» загружает базу ПК, Диск не меняется', async () => {
    const phone = freshPhone();
    prepare(phone);
    const before = await diskMeta();
    const { impl, asked } = ui({ pull: true });
    expect(await syncInteractive(phone.sync, host, impl, online, { announce: true })).toBe(true);
    // Ни конфликта, ни вопроса «выложить»; «загрузить?» допустимо (Диск «старше» состояния).
    expect(asked.filter((q) => q !== 'pull')).toEqual([]);
    expect(await diskMeta()).toBe(before);
    expect(phone.clientIds()).toEqual(['111', '222', '333']);
  });

  test('при запуске — вопрос «загрузить?», отказ ничего не выкладывает', async () => {
    const phone = freshPhone();
    prepare(phone);
    const before = await diskMeta();
    const action = await startupAction(phone.sync);
    expect(action).toEqual({ kind: 'sync', announce: false, assumeOffline: false });
    const { impl, asked } = ui({ pull: false });
    expect(await syncInteractive(phone.sync, host, impl, online, { confirmPull: true })).toBe(false);
    expect(asked).toEqual(['pull']);
    expect(await diskMeta()).toBe(before);
    expect(pc.run<string[]>('client_ids')).toEqual(['111', '222', '333']);
  });

  test('уход в фон ничего не выкладывает', async () => {
    const phone = freshPhone();
    prepare(phone);
    const before = await diskMeta();
    expect(await backgroundPush(phone.sync, host)).toBeNull();
    expect(await diskMeta()).toBe(before);
  });

  test('даже прямой вызов выгрузки отказывает', async () => {
    const phone = freshPhone();
    prepare(phone);
    phone.open(() => undefined); // нужен файл, иначе выгрузке нечего читать
    const before = await diskMeta();
    const meta = (await phone.sync.check()).meta;
    await expect(phone.sync.push(meta)).rejects.toThrow('пустая база');
    expect(await diskMeta()).toBe(before);
  });
});

describe('телефон отказался загружать базу и начал работать с нуля', () => {
  test('конфликт, а не тихая выгрузка; «Отмена» сохраняет Диск', async () => {
    const phone = freshPhone();
    phone.addClient('999');
    const before = await diskMeta();
    expect(await phone.status()).toBe('CONFLICT');
    expect(await backgroundPush(phone.sync, host)).toBeNull();
    const { impl, asked } = ui({ conflict: '' });
    expect(await syncInteractive(phone.sync, host, impl, online, { confirmPull: true })).toBe(false);
    expect(asked).toEqual(['conflict']);
    expect(await diskMeta()).toBe(before);
  });

  test('после «Оставить эту» база ПК остаётся на Диске прежней ревизией', async () => {
    const phone = freshPhone();
    phone.addClient('999');
    const { impl } = ui({ conflict: 'local' });
    expect(await syncInteractive(phone.sync, host, impl, online)).toBe(true);
    expect((await server.list()).some((p) => p.includes('rev000001_'))).toBe(true);
  });
});
