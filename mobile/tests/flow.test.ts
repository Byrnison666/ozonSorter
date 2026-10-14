/**
 * Сценарий синхронизации с диалогами (перенос ключевых случаев
 * tests/test_sync_ui_flow.py ПК): фейковый интерфейс, настоящий SyncService
 * и общий тестовый «Яндекс.Диск».
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';

import {
  backgroundPush, type DbHost, startupAction, syncAfterBackground, syncInteractive, synchronize, type SyncUi, texts,
} from '../src/core/sync/flow';
import { CancelToken, SyncCancelled, type SyncCheck } from '../src/core/sync/syncService';
import { type Progress, YaDiskClient } from '../src/core/sync/yadisk';
import { tempDir } from './support/pc';
import { type DavServer, Phone, startDavServer, testTransport } from './support/syncEnv';

class FakeUi implements SyncUi {
  calls: string[] = [];
  conflict: 'local' | 'remote' | '' = '';
  pull = true;
  emptyDisk = false;
  retries: boolean[] = [];
  cancelAt: string | null = null;
  messages: Array<[string, string]> = [];
  onRetry?: () => Promise<void>;

  transfers: Record<string, Array<[number, number]>> = {};

  async withProgress<T>(text: string, fn: (t: CancelToken, p: Progress) => Promise<T>): Promise<T> {
    this.calls.push(`progress:${text}`);
    if (this.cancelAt === text) throw new SyncCancelled();
    const log: Array<[number, number]> = (this.transfers[text] = []);
    return fn(new CancelToken(), (done, total) => log.push([done, total]));
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
    expect(await syncInteractive(a.sync, host, ui, online)).toBe('IN_SYNC');
    expect(ui.calls).toContain('retry:true:true');
    expect(ui.messages.at(-1)?.[1]).toContain('выложены на Яндекс.Диск');
  });

  test('нет связи: «Позже» — Диск недоступен, без сообщений об ошибке', async () => {
    await server.control({ cmd: 'set', offline: true });
    ui.retries = [false];
    expect(await syncInteractive(a.sync, host, ui, async () => false)).toBe('UNAVAILABLE');
    expect(ui.calls).toContain('retry:false:false');
    expect(ui.messages).toEqual([]);
  });

  test('отозванный токен — сообщение войти заново', async () => {
    await server.control({ cmd: 'set', token: 'other-token' });
    try {
      expect(await syncInteractive(a.sync, host, ui, online)).toBe('NOT_SYNCED');
    } finally {
      await server.control({ cmd: 'set', token: 'test-token' });
    }
    expect(ui.messages[0]).toEqual(['error', expect.stringContaining('Войдите в Яндекс заново') as unknown as string]);
  });

  test('выгрузка и загрузка показывают ход передачи', async () => {
    a.addClient('1');
    await synchronize(a.sync, host, ui);
    const up = ui.transfers['Выгрузка базы на Яндекс.Диск…'];
    expect(up.length).toBeGreaterThan(0);
    await synchronize(b.sync, host, ui);
    const down = ui.transfers['Загрузка базы с Яндекс.Диска…'];
    expect(down[down.length - 1]).toEqual(up[up.length - 1]);
    // Проверка Диска передачи файла базы не показывает.
    expect(ui.transfers['Проверка Яндекс.Диска…']).toEqual([]);
  });

  test('ручной запуск сообщает об успехе, автоматический — нет', async () => {
    expect(await syncInteractive(a.sync, host, ui, online, { announce: true })).toBe('IN_SYNC');
    expect(ui.messages.length).toBe(1);
    ui.messages = [];
    expect(await syncInteractive(a.sync, host, ui, online)).toBe('IN_SYNC');
    expect(ui.messages).toEqual([]);
  });

  test('после отказа от выбора версии — не «синхронизировано»', async () => {
    await seed();
    a.addClient('222');
    await a.push();
    b.addClient('333');
    expect(await syncInteractive(b.sync, host, ui, online)).toBe('NOT_SYNCED');
  });

  test('ручной запуск при сбое Яндекса объясняет, что работать можно', async () => {
    a.addClient('111');
    await server.control({ cmd: 'set', server_error: 500 });
    expect(await syncInteractive(a.sync, host, ui, online, { announce: true })).toBe('UNAVAILABLE');
    expect(ui.messages).toEqual([['warning', expect.stringContaining('работать можно') as unknown as string]]);
    expect(ui.calls.filter((c) => c.startsWith('retry'))).toEqual([]);
  });
});

describe('Яндекс.Диск недоступен: автоматические попытки молчат', () => {
  for (const [name, flags] of [
    ['сбой на стороне Яндекса (500)', { server_error: 500 }],
    ['слишком много запросов (429)', { server_error: 429 }],
    ['нет связи', { offline: true }],
  ] as const) {
    test(`${name}: без окон, база телефона цела`, async () => {
      await seed();
      a.addClient('222');                       // импорт или «Отгрузка» на телефоне
      await server.control({ cmd: 'set', ...flags });
      expect(await syncInteractive(a.sync, host, ui, online, { confirmPull: true, quiet: true }))
        .toBe('UNAVAILABLE');
      expect(ui.messages).toEqual([]);
      expect(ui.calls.filter((c) => c.startsWith('retry'))).toEqual([]);
      expect(host.log.at(-1)).toBe('reload');   // база снова открыта — работа продолжается
      expect(a.clientIds()).toEqual(['111', '222']);
      expect(await a.sync.localChanged()).toBe(true);
    });
  }

  test('когда Диск заработал, изменения выкладываются', async () => {
    await seed();
    a.addClient('222');
    await server.control({ cmd: 'set', server_error: 503 });
    expect(await syncInteractive(a.sync, host, ui, online, { quiet: true })).toBe('UNAVAILABLE');
    await server.control({ cmd: 'set', server_error: null });
    expect(await syncInteractive(a.sync, host, ui, online, { quiet: true })).toBe('IN_SYNC');
    await b.pull();
    expect(b.clientIds()).toEqual(['111', '222']);
  });

  test('короткий сбой переживает повтор запроса', async () => {
    a.addClient('111');
    await server.control({ cmd: 'fail_next', method: 'GET', code: 502 });
    expect(await syncInteractive(a.sync, host, ui, online, { quiet: true })).toBe('IN_SYNC');
  });

  test('кончилось место — не временный сбой, окно показывается', async () => {
    a.addClient('111');
    await server.control({ cmd: 'fail_next', method: 'GET', code: 507 });
    expect(await syncInteractive(a.sync, host, ui, online, { quiet: true })).toBe('NOT_SYNCED');
    expect(ui.messages[0]?.[1]).toContain('закончилось место');
  });

  test('отозванный вход не прячется', async () => {
    await server.control({ cmd: 'set', token: 'other-token' });
    try {
      expect(await syncInteractive(a.sync, host, ui, online, { quiet: true })).toBe('NOT_SYNCED');
    } finally {
      await server.control({ cmd: 'set', token: 'test-token' });
    }
    expect(ui.messages[0]?.[1]).toContain('Войдите в Яндекс заново');
  });
});

describe('испорченный ответ Диска — как недоступность', () => {
  for (const [name, flags] of [
    ['API отвечает страницей вместо JSON', { garbage_api: true }],
    ['хранилище отдаёт страницу вместо файла', { garbage_gets: true }],
  ] as const) {
    test(`${name}: тихая попытка — без окон, база телефона цела`, async () => {
      await seed();
      a.addClient('222');
      await server.control({ cmd: 'set', ...flags });
      expect(await syncInteractive(a.sync, host, ui, online, { confirmPull: true, quiet: true }))
        .toBe('UNAVAILABLE');
      expect(ui.messages).toEqual([]);
      expect(ui.calls.filter((c) => c.startsWith('retry'))).toEqual([]);
      expect(host.log.at(-1)).toBe('reload');
      expect(a.clientIds()).toEqual(['111', '222']);
      expect(await a.sync.localChanged()).toBe(true);
    });
  }

  /** Ревизия 2 выложена с B; её файл на Диске подменяется content. */
  async function spoilRevision(content: Uint8Array) {
    await seed();
    b.addClient('222');
    await b.push();
    const file = (await server.list()).find((f) => /ozon_sorter_rev000002_.*\.db\.gz$/.test(f))!;
    await server.put(file, content);
  }

  for (const [name, content] of [
    ['сумма загруженной базы не совпала', () => gzipSync(new TextEncoder().encode('другая база'))],
    ['файл ревизии — не gzip', () => new TextEncoder().encode('<html>502</html>')],
  ] as const) {
    test(`${name}: тихая загрузка — без окон, база телефона цела`, async () => {
      await spoilRevision(content());
      expect(await syncInteractive(a.sync, host, ui, online, { confirmPull: true, quiet: true }))
        .toBe('UNAVAILABLE');
      expect(ui.messages).toEqual([]);
      expect(host.log.at(-1)).toBe('reload');
      expect(a.clientIds()).toEqual(['111']);
    });
  }

  test('ручной запуск показывает окно: непонятный ответ, работать можно', async () => {
    a.addClient('111');
    await server.control({ cmd: 'set', garbage_api: true });
    expect(await syncInteractive(a.sync, host, ui, online, { announce: true })).toBe('UNAVAILABLE');
    expect(ui.messages).toHaveLength(1);
    expect(ui.messages[0][0]).toBe('warning');
    expect(ui.messages[0][1]).toContain('непонятный ответ');
    expect(ui.messages[0][1]).toContain('работать можно');
    expect(ui.calls.filter((c) => c.startsWith('retry'))).toEqual([]);
  });

  test('ручной запуск: испорченная ревизия — окно, база цела', async () => {
    await spoilRevision(new TextEncoder().encode('<html>502</html>'));
    expect(await syncInteractive(a.sync, host, ui, online, { announce: true })).toBe('UNAVAILABLE');
    expect(ui.messages).toHaveLength(1);
    expect(ui.messages[0][1]).toContain('работать можно');
    expect(a.clientIds()).toEqual(['111']);
  });
});

describe('запуск и уход в фон', () => {
  test('при запуске: совпадает — ничего; правки с прошлого раза — выложить с сообщением', async () => {
    expect(await startupAction(a.sync)).toEqual({ kind: 'none' });
    a.addClient('111');
    expect(await startupAction(a.sync)).toEqual({ kind: 'sync', announce: true });
  });

  test('при запуске Диск недоступен — без окон, есть правки или нет', async () => {
    for (const flags of [{ offline: true }, { server_error: 500 }]) {
      await server.control({ cmd: 'set', offline: false, server_error: null, ...flags });
      expect(await startupAction(a.sync)).toEqual({ kind: 'unavailable' });
    }
    a.addClient('111');
    expect(await startupAction(a.sync)).toEqual({ kind: 'unavailable' });
  });

  test('последняя синхронизация запоминается', async () => {
    expect(await a.sync.lastSyncedAt()).toBe('');
    a.addClient('111');
    await a.push();
    expect(await a.sync.lastSyncedAt()).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/);
  });

  test('при запуске с битым meta.json — как недоступность, без окна', async () => {
    await server.put('/OzonSorter/meta.json', '{oops');
    expect(await startupAction(a.sync)).toEqual({ kind: 'unavailable' });
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

  test('в фоне Диск недоступен — приложение не падает, база снова открыта', async () => {
    a.addClient('111');
    for (const flags of [{ offline: true }, { server_error: 500 }, { garbage_api: true }]) {
      await server.control({ cmd: 'set', offline: false, server_error: null, garbage_api: false, ...flags });
      expect(await backgroundPush(a.sync, host)).toBe('UNAVAILABLE');
      expect(host.log.at(-1)).toBe('reload');
    }
    expect(existsSync(`${a.dbPath}.snapshot`)).toBe(false);
  });

  test('в фоне выгружается снимок: база закрыта только на время копии, правки во время выгрузки — невыложенные', async () => {
    await seed();
    // Запросы к Диску отмечаются в том же журнале, что закрытие и открытие базы.
    const recording = {
      request: (req: Parameters<typeof testTransport.request>[0]) => {
        host.log.push('net');
        return testTransport.request(req);
      },
    };
    const p = new Phone(b.dir, server, {
      client: new YaDiskClient('test-token', server.apiUrl, recording, 5_000, [0, 0]),
    });
    p.addClient('222');
    const rev = await backgroundPush(p.sync, host, () => {
      host.log.push('snapshot');
      p.addClient('333'); // база снова открыта: правка, пока снимок уходит на Диск
    });
    expect(rev).toBe(2);
    expect(host.log.slice(0, 3)).toEqual(['release', 'reload', 'snapshot']);
    expect(host.log.slice(3).every((e) => e === 'net')).toBe(true); // обмен — уже с открытой базой
    expect(host.log.length).toBeGreaterThan(3);
    expect(existsSync(`${p.dbPath}.snapshot`)).toBe(false);
    expect(await p.sync.localChanged()).toBe(true); // правка 333 не выложена
    await a.pull();
    expect(a.clientIds()).toEqual(['111', '222']);
    await p.push();
    await a.pull();
    expect(a.clientIds()).toEqual(['111', '222', '333']);
  });

  test('в фоне без правок во время выгрузки база совпадает с выложенной', async () => {
    await seed();
    b.addClient('222');
    expect(await backgroundPush(b.sync, host)).toBe(2);
    expect(await b.sync.localChanged()).toBe(false); // побайтный снимок — та же сумма
    expect(await b.status()).toBe('UP_TO_DATE');
  });

  test('в фоне незавершённая запись — снимка нет, база не закрывалась', async () => {
    await seed();
    b.addClient('222');
    writeFileSync(`${b.dbPath}-journal`, '');
    try {
      expect(await backgroundPush(b.sync, host, () => host.log.push('snapshot'))).toBeNull();
    } finally {
      rmSync(`${b.dbPath}-journal`);
    }
    expect(host.log).toEqual([]); // отказ ещё до закрытия базы
    expect(existsSync(`${b.dbPath}.snapshot`)).toBe(false);
  });

  test('повреждённый файл состояния не мешает проверке', async () => {
    await seed();
    writeFileSync(join(b.dir, 'sync_state.json'), '{broken');
    expect(await startupAction(b.sync)).toEqual({ kind: 'none' }); // содержимое совпадает
  });
});

describe('обмен сразу после фоновой выгрузки', () => {
  const base = { changed: true, active: true, externalOpen: 0, now: 1000, autoSyncAfter: 0 };

  test('во время выгрузки база менялась — обмен сразу', () => {
    expect(syncAfterBackground(base)).toBe(true);
  });

  test.each([
    ['правок не было', { changed: false }],
    ['приложение не на экране', { active: false }],
    ['открыт выбор файла или «Поделиться»', { externalOpen: 1 }],
    ['пауза после недоступного Диска', { autoSyncAfter: 2000 }],
  ])('%s — обмена нет', (_name, over) => {
    expect(syncAfterBackground({ ...base, ...over })).toBe(false);
  });
});
