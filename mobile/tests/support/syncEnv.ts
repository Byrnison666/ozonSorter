/**
 * Окружение перекрёстных тестов синхронизации: общий тестовый WebDAV-сервер
 * ПК, «ПК» (настоящий SyncService на Python) и «телефон» (SyncService на TS).
 */
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import * as fs from 'node:fs/promises';
import { dirname, join } from 'node:path';

import { addClient } from '../../src/core/clients';
import type { Db } from '../../src/core/db';
import { systemClock } from '../../src/core/models';
import { configureConnection, ensureSchema } from '../../src/core/schema';
import {
  type LocalFiles, type SyncDeps, SyncService, type SyncOptions,
} from '../../src/core/sync/syncService';
import { fetchTransport, type Transport, WebDavClient } from '../../src/core/sync/webdav';
import { openBunDb } from './bunDb';
import { PY_SCRIPTS, PYTHON, pythonEnv, REPO_ROOT, runPythonScript } from './pc';

export const REMOTE_DIR = 'OzonSorter';
export const META_PATH = `/${REMOTE_DIR}/meta.json`;

export interface DavServer {
  url: string;
  control<T = Record<string, unknown>>(cmd: Record<string, unknown>): Promise<T>;
  /** Положить файл на «Диск» в обход клиентов (как мог бы кто угодно с доступом). */
  put(path: string, data: Uint8Array | string): Promise<void>;
  get(path: string): Promise<Uint8Array | null>;
  list(): Promise<string[]>;
  stop(): Promise<void>;
}

export async function startDavServer(): Promise<DavServer> {
  const proc = Bun.spawn([PYTHON, join(PY_SCRIPTS, 'webdav_server.py')], {
    cwd: REPO_ROOT, env: pythonEnv(), stdin: 'pipe', stdout: 'pipe', stderr: 'inherit',
  });
  const reader = proc.stdout.getReader();
  let text = '';
  while (!text.includes('\n')) {
    const { value, done } = await reader.read();
    if (done) throw new Error('webdav_server.py exited before start');
    text += new TextDecoder().decode(value);
  }
  reader.releaseLock();
  const { url, control: controlUrl } = JSON.parse(text.split('\n')[0]);
  const control = async <T>(cmd: Record<string, unknown>): Promise<T> => {
    const r = await fetch(controlUrl, {
      method: 'POST', body: JSON.stringify(cmd), headers: { Connection: 'close' },
    });
    return (await r.json()) as T;
  };
  return {
    url,
    control,
    async put(path, data) {
      const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data;
      await control({ cmd: 'put', path, b64: Buffer.from(bytes).toString('base64') });
    },
    async get(path) {
      const { b64 } = await control<{ b64: string | null }>({ cmd: 'get', path });
      return b64 === null ? null : new Uint8Array(Buffer.from(b64, 'base64'));
    },
    async list() {
      return (await control<{ files: string[] }>({ cmd: 'list' })).files;
    },
    async stop() {
      proc.stdin.end();
      await proc.exited;
    },
  };
}

/**
 * Тестовые серверы на http.server Python (HTTP/1.0) закрывают соединение после
 * ответа, а fetch Bun иногда берёт его из пула повторно — ECONNRESET. Отказ от
 * keep-alive убирает гонку; к телефону и Яндекс.Диску это не относится.
 */
const testTransport: Transport = {
  request: (req) => fetchTransport.request({ ...req, headers: { ...req.headers, Connection: 'close' } }),
};

export const nodeFiles: LocalFiles = {
  exists: async (p) => existsSync(p),
  read: async (p) => new Uint8Array(await fs.readFile(p)),
  readText: (p) => fs.readFile(p, 'utf-8'),
  async write(p, data) {
    const handle = await fs.open(p, 'w');
    try {
      await handle.writeFile(data);
      await handle.sync();
    } finally {
      await handle.close();
    }
  },
  replace: (src, dst) => fs.rename(src, dst),
  copy: (src, dst) => fs.copyFile(src, dst),
  remove: (p) => fs.rm(p, { force: true }),
  list: (dir) => fs.readdir(dir),
  mkdirs: async (dir) => {
    await fs.mkdir(dir, { recursive: true });
  },
  join,
  dirname,
};

export function sha256Hex(data: Uint8Array): string {
  return createHash('sha256').update(data).digest('hex');
}

export const nodeDeps: SyncDeps = {
  files: nodeFiles,
  sha256: async (data) => sha256Hex(data),
  openDb: openBunDb,
  clock: systemClock,
};

/** «Телефон»: своя папка, база и состояние синхронизации. */
export class Phone {
  readonly dbPath: string;
  sync: SyncService;

  constructor(
    readonly dir: string, readonly server: DavServer,
    opts: Partial<SyncOptions> = {}, deps: SyncDeps = nodeDeps,
  ) {
    this.dbPath = join(dir, 'ozon_sorter.db');
    this.sync = new SyncService(deps, {
      dbPath: this.dbPath,
      client: new WebDavClient(server.url, 'user', 'secret', testTransport, 5_000),
      remoteDir: REMOTE_DIR,
      statePath: join(dir, 'sync_state.json'),
      deviceName: 'Телефон',
      targetId: 't1',
      ...opts,
    });
  }

  /** Открыть базу, как приложение при запуске (создаёт схему в новой). */
  open<T>(fn: (db: Db) => T): T {
    const db = openBunDb(this.dbPath);
    configureConnection(db);
    ensureSchema(db);
    try {
      return fn(db);
    } finally {
      db.close();
    }
  }

  addClient(ozonId: string): void {
    this.open((db) => {
      const r = addClient(db, { ozonClientId: ozonId, fullName: 'Телефон', phone: '', point: 'KOMSOMOLSKAYA_4' });
      if (!r.ok) throw new Error(r.error);
    });
  }

  clientIds(): string[] {
    return this.open((db) => db.all<{ ozon_client_id: string }>(
      'SELECT ozon_client_id FROM clients ORDER BY ozon_client_id',
    ).map((r) => r.ozon_client_id));
  }

  async push(): Promise<number> {
    return this.sync.push((await this.sync.check()).meta);
  }

  async pull(): Promise<number> {
    return this.sync.pull((await this.sync.check()).meta);
  }

  async status(): Promise<string> {
    return (await this.sync.check()).status;
  }
}

/** «ПК»: настоящий код ПК (tests/py/pc_device.py) в своей папке. */
export class Pc {
  constructor(readonly dir: string, readonly server: DavServer) {}

  run<T = unknown>(cmd: string, arg?: string): T {
    const args = [this.dir, this.server.url, cmd, ...(arg === undefined ? [] : [arg])];
    return JSON.parse(runPythonScript('pc_device.py', ...args)) as T;
  }

  get dbPath(): string {
    return join(this.dir, 'ozon_sorter.db');
  }
}
