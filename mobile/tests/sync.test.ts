/**
 * Обмен базой между ПК (настоящий SyncService на Python) и телефоном через
 * общий тестовый WebDAV-сервер: протокол на Диске должен совпадать.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { runPython, tempDir } from './support/pc';
import { type DavServer, META_PATH, Pc, Phone, sha256Hex, startDavServer } from './support/syncEnv';

type PcCheck = { status: string; meta: { rev: number; device: string; saved_at: string } | null; remote_is_older: boolean };

describe('обмен базой ПК ↔ телефон', () => {
  let server: DavServer;
  let root: ReturnType<typeof tempDir>;
  let pc: Pc;
  let phone: Phone;

  const phoneSha = () => sha256Hex(new Uint8Array(readFileSync(phone.dbPath)));

  beforeAll(async () => {
    server = await startDavServer();
  });
  afterAll(async () => {
    await server.stop();
    root?.cleanup();
  });
  beforeEach(async () => {
    await server.control({ cmd: 'clear' });
    root?.cleanup();
    root = tempDir('ozon-sync-');
    pc = new Pc(join(root.path, 'pc'), server);
    phone = new Phone(root.path, server);
    pc.run('init');
  });

  test('телефон забирает базу ПК, ПК — базу телефона', async () => {
    pc.run('add_client', '111');
    expect(pc.run<number>('push')).toBe(1);

    expect(await phone.status()).toBe('REMOTE_AHEAD'); // базы на телефоне ещё нет
    expect(await phone.pull()).toBe(1);
    expect(phoneSha()).toBe(pc.run<string>('sha'));
    expect(phone.clientIds()).toEqual(['111']);
    // Открытие базы приложением не меняет файл — иначе каждая загрузка выглядела бы правкой.
    expect(await phone.status()).toBe('UP_TO_DATE');

    phone.addClient('222');
    expect(await phone.status()).toBe('LOCAL_AHEAD');
    expect(await phone.push()).toBe(2);

    const check = pc.run<PcCheck>('check');
    expect(check.status).toBe('REMOTE_AHEAD');
    expect(check.meta?.device).toBe('Телефон');
    expect(check.meta?.saved_at).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/);
    expect(pc.run<number>('pull')).toBe(2);
    expect(pc.run<string[]>('client_ids')).toEqual(['111', '222']);
    expect(pc.run<string>('sha')).toBe(phoneSha());

    // ПК открывает базу телефона с миграциями — файл не меняется.
    pc.run('reopen');
    expect(pc.run<string>('sha')).toBe(phoneSha());
    expect(pc.run<boolean>('local_changed')).toBe(false);
    expect(pc.run<PcCheck>('check').status).toBe('UP_TO_DATE');
  });

  test('первым выкладывает телефон — ПК принимает его базу', async () => {
    phone.addClient('333');
    expect(await phone.status()).toBe('LOCAL_AHEAD');
    expect(await phone.push()).toBe(1);
    expect(pc.run<PcCheck>('check').status).toBe('REMOTE_AHEAD'); // пустая база ПК
    pc.run('pull');
    expect(pc.run<string[]>('client_ids')).toEqual(['333']);
    pc.run('reopen');
    expect(pc.run<PcCheck>('check').status).toBe('UP_TO_DATE');
  });

  test('meta.json телефона — в формате ПК', async () => {
    phone.addClient('444');
    await phone.push();
    const text = new TextDecoder().decode((await server.get(META_PATH))!);
    const redumped = runPython(
      'import json, sys\nprint(json.dumps(json.loads(sys.argv[1]), ensure_ascii=False), end="")',
      text,
    );
    expect(text).toBe(redumped);
    expect(Object.keys(JSON.parse(text))).toEqual(['rev', 'file', 'sha256', 'schema_version', 'device', 'saved_at']);
  });

  test('конфликт видят обе стороны; выбор телефона доходит до ПК', async () => {
    pc.run('add_client', '111');
    pc.run('push');
    await phone.pull();

    pc.run('add_client', '222');
    phone.addClient('333');
    expect(pc.run<number>('push')).toBe(2);
    const check = await phone.sync.check();
    expect(check.status).toBe('CONFLICT');
    expect(check.localSyncedAt).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/);

    expect(await phone.sync.push(check.meta)).toBe(3); // «оставить эту»
    expect(pc.run<PcCheck>('check').status).toBe('REMOTE_AHEAD');
    pc.run('pull');
    expect(pc.run<string[]>('client_ids')).toEqual(['111', '333']);
    // Вытесненная версия ПК осталась на Диске отдельной ревизией.
    expect((await server.list()).some((p) => p.includes('rev000002_'))).toBe(true);
  });

  test('конфликт на ПК, когда первым выложил телефон', async () => {
    pc.run('add_client', '111');
    pc.run('push');
    await phone.pull();
    phone.addClient('222');
    pc.run('add_client', '333');
    await phone.push();
    expect(pc.run<PcCheck>('check').status).toBe('CONFLICT');
  });

  test('ежедневная копия общая для ПК и телефона: одна за день, не перезаписывается', async () => {
    const d = new Date();
    const today = `/OzonSorter/backups/ozon_sorter_${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}.db.gz`;
    pc.run('add_client', '111');
    pc.run('push');
    const pcCopy = await server.get(today);
    expect(pcCopy).not.toBeNull();
    await phone.pull();
    phone.addClient('222');
    await phone.push();
    expect(await server.get(today)).toEqual(pcCopy);
    expect((await server.list()).filter((p) => p.startsWith('/OzonSorter/backups/'))).toEqual([today]);
  });

  test('на Диске остаются 5 последних ревизий, кто бы ни выкладывал', async () => {
    pc.run('add_client', '100');
    pc.run('push');
    for (let i = 1; i <= 6; i++) {
      if (i % 2) {
        await phone.pull();
        phone.addClient(String(100 + i));
        await phone.push();
      } else {
        pc.run('pull');
        pc.run('add_client', String(100 + i));
        pc.run('push');
      }
    }
    const revs = (await server.list()).filter((p) => p.startsWith('/OzonSorter/ozon_sorter_rev'))
      .map((p) => Number(/rev(\d{6})_/.exec(p)![1])).sort((a, b) => a - b);
    expect(revs).toEqual([3, 4, 5, 6, 7]);
  });
});
