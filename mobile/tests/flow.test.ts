/**
 * Сценарий синхронизации с диалогами (перенос ключевых случаев
 * tests/test_sync_ui_flow.py ПК): фейковый интерфейс, настоящий SyncService
 * и общий тестовый WebDAV-сервер.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  backgroundPush, type DbHost, startupAction, syncInteractive, synchronize, type SyncUi, texts,
} from '../src/core/sync/flow';
import { CancelToken, SyncCancelled, type SyncCheck } from '../src/core/sync/syncService';
import { tempDir } from './support/pc';
import { type DavServer, Phone, startDavServer } from './support/syncEnv';

class FakeUi implements SyncUi {
  calls: string[] = [];
  conflict: 'local' | 'remote' | '' = '';
  pull = true;
  emptyDisk = false;
  retries: boolean[] = [];
  cancelAt: string | null = null;
  messages: Array<[string, string]> = [];
  onRetry?: () => Promise<void>;

  async withProgress<T>(text: string, fn: (t: CancelToken) => Promise<T>): Promise<T> {
    this.calls.push(`progress:${text}`);
    if (this.cancelAt === text) throw new SyncCancelled();
    return fn(new CancelToken());
  }
  async askConflict(check: SyncCheck) {
    this.calls.push(`conflict:${texts.conflict(check)}`);
    return this.conflict;
  }
  async askPull(check: SyncCheck) {
    this.calls.push(`pull:${texts.pull(check)}`);
    return this.pull;
  }
  async askPushToEmptyDisk() {
    this.calls.push('emptyDisk');
    return this.emptyDisk;
  }
  async askRetryOffline(hasChanges: boolean, internetOk: boolean | null) {
    this.calls.push(`retry:${hasChanges}:${internetOk}`);
    const answer = this.retries.shift() ?? false;
    if (answer) await this.onRetry?.();
    return answer;
  }
  async showMessage(kind: 'info' | 'warning' | 'error', title: string, text: string) {
    this.messages.push([kind, text]);
  }
}

class FakeHost implements DbHost {
  log: string[] = [];
  failReload = 0;
  release() {
    this.log.push('release');
  }
  reload() {
    this.log.push('reload');
    if (this.failReload > 0) {
      this.failReload--;
      throw new Error('cannot open');
    }
  }
}

let server: DavServer;
let root: ReturnType<typeof tempDir>;
let a: Phone;
let b: Phone;
let ui: FakeUi;
let host: FakeHost;
let n = 0;

function phone(name: string): Phone {
  const dir = join(root.path, `${name}-${++n}`);
  mkdirSync(dir, { recursive: true });
  const p = new Phone(dir, server);
  p.open(() => undefined);
  return p;
}

beforeAll(async () => {
  server = await startDavServer();
  root = tempDir('ozon-flow-');
});
afterAll(async () => {
  await server.stop();
  root.cleanup();
});
beforeEach(async () => {
  await server.control({ cmd: 'clear' });
  await server.control({ cmd: 'set', offline: false });
  a = phone('a');
  b = phone('b');
  ui = new FakeUi();
  host = new FakeHost();
});

async function seed() {
  a.addClient('111');
  await a.push();
  await b.pull();
}

const online = async () => true;

describe('один проход синхронизации', () => {
  test('совпадает — ничего не делаем; база закрывается и открывается', async () => {
    const r = await synchronize(a.sync, host, ui);
    expect(r.outcome).toBe('UNCHANGED');
    expect(host.log).toEqual(['release', 'reload']);
  });

  test('конфликт: «Оставить эту» выкладывает, в окне — время синхронизации', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    b.addClient('333');
    ui.conflict = 'local';
    const r = await synchronize(b.sync, host, ui);
    expect(r.outcome).toBe('PUSHED');
    const shown = ui.calls.find((c) => c.startsWith('conflict:'))!;
    expect(shown).toMatch(/последняя синхронизация \d\d\.\d\d\.\d{4} \d\d:\d\d/);
    expect(shown).toContain('«Телефон», сохранена');
    await a.pull();
    expect(a.clientIds()).toEqual(['111', '333']);
  });

  test('конфликт: «Взять с Диска» загружает; «Отмена» ничего не меняет', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    b.addClient('333');
    ui.conflict = '';
    expect((await synchronize(b.sync, host, ui)).outcome).toBe('DECLINED');
    expect(b.clientIds()).toEqual(['111', '333']);
    ui.conflict = 'remote';
    expect((await synchronize(b.sync, host, ui)).outcome).toBe('PULLED');
    expect(b.clientIds()).toEqual(['111', '222']);
  });

  test('автоматический запуск спрашивает перед загрузкой', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    ui.pull = false;
    expect((await synchronize(b.sync, host, ui, true)).outcome).toBe('DECLINED');
    expect(b.clientIds()).toEqual(['111']);
    expect((await synchronize(b.sync, host, ui, false)).outcome).toBe('PULLED');
  });

  test('Диск очистили: спрашиваем, выкладывать ли заново', async () => {
    await seed();
    await server.control({ cmd: 'clear' });
    expect((await synchronize(b.sync, host, ui)).outcome).toBe('DECLINED');
    ui.emptyDisk = true;
    expect((await synchronize(b.sync, host, ui)).outcome).toBe('PUSHED');
  });

  test('загруженная база не открылась — возвращается прежняя', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    host.failReload = 1;
    await expect(synchronize(b.sync, host, ui)).rejects.toThrow('возвращена прежняя база');
    expect(b.clientIds()).toEqual(['111']);
    expect(host.log.at(-1)).toBe('reload');
  });

  test('отмена проверки — база снова открыта', async () => {
    ui.cancelAt = 'Проверка Яндекс.Диска…';
    await expect(synchronize(a.sync, host, ui)).rejects.toBeInstanceOf(SyncCancelled);
    expect(host.log).toEqual(['release', 'reload']);
  });
});

describe('синхронизация с диалогами', () => {
  test('нет связи: «Повторить», когда связь вернулась, — успех и сообщение', async () => {
    a.addClient('111');
    await server.control({ cmd: 'set', offline: true });
    ui.retries = [true];
    ui.onRetry = async () => {
      await server.control({ cmd: 'set', offline: false });
    };
    expect(await syncInteractive(a.sync, host, ui, online)).toBe(true);
    expect(ui.calls).toContain('retry:true:true');
    expect(ui.messages.at(-1)?.[1]).toContain('выложены на Яндекс.Диск');
  });

  test('нет связи: «Позже» — false без сообщений об ошибке', async () => {
    await server.control({ cmd: 'set', offline: true });
    ui.retries = [false];
    expect(await syncInteractive(a.sync, host, ui, async () => false)).toBe(false);
    expect(ui.calls).toContain('retry:false:false');
    expect(ui.messages).toEqual([]);
  });

  test('ошибка авторизации — сообщение про логин и пароль', async () => {
    await server.control({ cmd: 'fail_next', method: 'GET', code: 401 });
    expect(await syncInteractive(a.sync, host, ui, online)).toBe(false);
    expect(ui.messages[0]).toEqual(['error', expect.stringContaining('логин или пароль') as unknown as string]);
  });

  test('ручной запуск сообщает об успехе, автоматический — нет', async () => {
    expect(await syncInteractive(a.sync, host, ui, online, { announce: true })).toBe(true);
    expect(ui.messages.length).toBe(1);
    ui.messages = [];
    expect(await syncInteractive(a.sync, host, ui, online)).toBe(true);
    expect(ui.messages).toEqual([]);
  });

  test('после отказа от выбора версии — не «синхронизировано»', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    b.addClient('333');
    expect(await syncInteractive(b.sync, host, ui, online)).toBe(false);
  });
});

describe('запуск и уход в фон', () => {
  test('при запуске: совпадает — ничего; правки с прошлого раза — выложить с сообщением', async () => {
    expect(await startupAction(a.sync)).toEqual({ kind: 'none' });
    a.addClient('111');
    expect(await startupAction(a.sync)).toEqual({ kind: 'sync', announce: true, assumeOffline: false });
  });

  test('при запуске без связи: молчим, если выкладывать нечего', async () => {
    await server.control({ cmd: 'set', offline: true });
    expect(await startupAction(a.sync)).toEqual({ kind: 'none' });
    a.addClient('111');
    expect(await startupAction(a.sync)).toEqual({ kind: 'sync', announce: false, assumeOffline: true });
  });

  test('при запуске с битым meta.json — предупреждение', async () => {
    await server.put('/OzonSorter/meta.json', '{oops');
    const action = await startupAction(a.sync);
    expect(action.kind).toBe('warn');
  });

  test('в фоне выкладываем только когда Диск не менялся', async () => {
    await seed();
    b.addClient('222');
    expect(await backgroundPush(b.sync, host)).toBe(2);
    a.addClient('333'); // теперь у A конфликт
    expect(await backgroundPush(a.sync, host)).toBeNull();
    expect(a.clientIds()).toEqual(['111', '333']);
    expect(await backgroundPush(phone('c').sync, host)).toBeNull(); // нечего выкладывать
  });

  test('в фоне сбой сети не роняет приложение', async () => {
    a.addClient('111');
    await server.control({ cmd: 'set', offline: true });
    expect(await backgroundPush(a.sync, host)).toBeNull();
    expect(host.log.at(-1)).toBe('reload');
  });

  test('повреждённый файл состояния не мешает проверке', async () => {
    await seed();
    writeFileSync(join(b.dir, 'sync_state.json'), '{broken');
    expect(await startupAction(b.sync)).toEqual({ kind: 'none' }); // содержимое совпадает
  });
});
