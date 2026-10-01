/**
 * Синхронизация базы через WebDAV: обмен целым файлом SQLite — перенос
 * src/sync_service.py. Протокол на Диске общий с ПК и должен совпадать с ним
 * (проверяет tests/sync.test.ts обменом между кодом ПК и телефона).
 *
 * Рассчитано на работу по очереди: слияния нет; если изменены обе стороны,
 * пользователь выбирает версию. Всё, что приходит с Диска, — недоверенное.
 */
import { Gunzip, gzipSync } from 'fflate';

import type { Db } from '../db';
import type { Clock } from '../models';
import { PyDateTime } from '../py';
import { SCHEMA_VERSION } from '../schema';
import { WebDavClient, WebDavError } from './webdav';

export const META_NAME = 'meta.json';
export const KEEP_REMOTE_REVISIONS = 5;
export const KEEP_PRESYNC_BACKUPS = 10;
// Ежедневные копии в отдельной папке: очистка ревизий их не трогает, и цепочка
// ошибочных выгрузок за один день не вытеснит вчерашнюю базу.
export const BACKUP_DIR = 'backups';
export const KEEP_DAILY_BACKUPS = 30;
const DAILY_RE = /^ozon_sorter_\d{4}-\d{2}-\d{2}\.db\.gz$/;
const EMPTY_BASE_ERROR =
  'На этом устройстве пустая база: выкладывать её поверх данных на Диске нельзя. Загрузите базу с Диска.';
const NEVER_SYNCED_ERROR =
  'Это устройство ещё ни разу не загружало базу с Диска: заменять ею базу на Диске нельзя. Загрузите базу с Диска.';
const MAX_META_BYTES = 64 * 1024;
/** Распакованная база больше этого — не наша (защита от gzip-бомбы). */
export const MAX_DB_BYTES = 512 * 1024 * 1024;
const MAX_REV = 999_998;
// Хвост sha в имени: два устройства с одним номером ревизии не затрут файлы друг друга.
const REVISION_RE = /^ozon_sorter_rev(\d{6})_([0-9a-f]{8})\.db\.gz$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const SAVED_AT_RE = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/;
/** Таблицы программы (модели ПК) и служебные SQLite; остальное — постороннее. */
const ALLOWED_TABLES = new Set([
  'clients', 'import_sessions', 'shipments', 'export_sessions', 'sqlite_sequence', 'sqlite_stat1',
]);

export const SyncStatus = {
  UP_TO_DATE: 'UP_TO_DATE',
  LOCAL_AHEAD: 'LOCAL_AHEAD',       // изменения только здесь — выложить
  REMOTE_AHEAD: 'REMOTE_AHEAD',     // на Диске другая версия, здесь правок нет — скачать
  CONFLICT: 'CONFLICT',             // изменены обе стороны
  REMOTE_MISSING: 'REMOTE_MISSING', // с Диска пропала база, с которой уже синхронизировались
} as const;
export type SyncStatus = (typeof SyncStatus)[keyof typeof SyncStatus];

/** Синхронизацию нельзя выполнить; текст показывается пользователю. */
export class SyncError extends Error {}

/** Пользователь отменил операцию до того, как она что-либо изменила. */
export class SyncCancelled extends Error {}

/**
 * Отмена долгой операции. Операция вызывает commit() перед необратимым шагом:
 * отмена раньше — прерывает её, позже — отклоняется.
 */
export class CancelToken {
  private cancelled = false;
  private committed = false;

  /** true — отмена принята; false — необратимый шаг уже начат. */
  cancel(): boolean {
    if (this.committed) return false;
    this.cancelled = true;
    return true;
  }

  commit(): void {
    if (this.cancelled) throw new SyncCancelled();
    this.committed = true;
  }
}

export interface RemoteMeta {
  rev: number;
  file: string;
  sha256: string;
  schema_version: number;
  device: string;
  saved_at: string;
}

export interface SyncCheck {
  status: SyncStatus;
  /** Текущая ревизия на Диске; null — Диск пуст. */
  meta: RemoteMeta | null;
  /** Диск не продолжает версию, с которой работало устройство. */
  remoteIsOlder: boolean;
  /** Когда устройство последний раз обменялось базой; '' — неизвестно. */
  localSyncedAt: string;
  /** Устройство ни разу не загружало и не выкладывало базу в это место Диска. */
  neverSynced: boolean;
}

/** Локальные файлы устройства. Пути — строки платформы. */
export interface LocalFiles {
  exists(path: string): Promise<boolean>;
  read(path: string): Promise<Uint8Array>;
  readText(path: string): Promise<string>;
  /** Записать и сбросить на диск. */
  write(path: string, data: Uint8Array | string): Promise<void>;
  /** Атомарно заменить dst файлом src. */
  replace(src: string, dst: string): Promise<void>;
  copy(src: string, dst: string): Promise<void>;
  remove(path: string): Promise<void>;
  list(dir: string): Promise<string[]>;
  mkdirs(dir: string): Promise<void>;
  join(dir: string, name: string): string;
  dirname(path: string): string;
}

export interface SyncDeps {
  files: LocalFiles;
  sha256(data: Uint8Array): Promise<string>;
  /** Открыть файл SQLite (для проверок и чтения). Закрывает вызывающий. */
  openDb(path: string): Db;
  clock: Clock;
}

interface State {
  base_rev: number;
  base_sha256: string;
  synced_at: string;
}

function sameRevision(a: RemoteMeta | null, b: RemoteMeta | null): boolean {
  if (a === null || b === null) return a === b;
  return a.rev === b.rev && a.sha256 === b.sha256;
}

/** Время в формате 'YYYY-MM-DD HH:MM:SS' (как strptime на ПК) либо ''. */
function validTime(value: unknown): string {
  if (typeof value !== 'string') return '';
  const m = SAVED_AT_RE.exec(value);
  if (!m) return '';
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number);
  const date = new Date(Date.UTC(y, mo - 1, d, h, mi, s));
  const ok = y >= 1 && date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1
    && date.getUTCDate() === d && h < 24 && mi < 60 && s < 60;
  return ok ? value : '';
}

function savedAtNow(clock: Clock): string {
  return clock.now().toString().slice(0, 19);
}

function isPyInt(v: unknown): v is number {
  return typeof v === 'number' && Number.isInteger(v);
}

/** Печатаемый символ по str.isprintable() Python. */
function isPrintable(ch: string): boolean {
  if (ch === ' ') return true;
  return !/[\p{C}\p{Z}]/u.test(ch);
}

export function parseMeta(raw: Uint8Array): RemoteMeta {
  const parsed: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('bad meta');
  const data = parsed as Record<string, unknown>;
  const { rev, file, sha256: sha, schema_version: schemaVersion } = data;
  if (!isPyInt(rev) || rev < 1 || rev > MAX_REV) throw new Error('bad rev');
  if (!isPyInt(schemaVersion) || schemaVersion < 0 || schemaVersion > 1_000_000) {
    throw new Error('bad schema_version');
  }
  if (typeof sha !== 'string' || !SHA256_RE.test(sha)) throw new Error('bad sha256');
  // Имя файла приходит с сервера — только наш формат, согласованный с rev и sha.
  const match = typeof file === 'string' ? REVISION_RE.exec(file) : null;
  if (!match || Number(match[1]) !== rev || match[2] !== sha.slice(0, 8)) {
    throw new Error('bad file name');
  }
  // Строки для диалогов — без управляющих символов и разметки.
  const deviceRaw: string = data.device === undefined ? '' : typeof data.device === 'string'
    ? data.device : String(JSON.stringify(data.device));
  const device = Array.from(deviceRaw).filter((ch) => isPrintable(ch) && !'<>&'.includes(ch))
    .slice(0, 64).join('');
  return { rev, file: match[0], sha256: sha, schema_version: schemaVersion, device, saved_at: validTime(data.saved_at) };
}

/** json.dumps(meta, ensure_ascii=False) — формат meta.json как у ПК. */
function dumpMeta(meta: RemoteMeta): string {
  const fields = (['rev', 'file', 'sha256', 'schema_version', 'device', 'saved_at'] as const)
    .map((k) => `${JSON.stringify(k)}: ${JSON.stringify(meta[k])}`);
  return `{${fields.join(', ')}}`;
}

/** Проверить файл базы до того, как он заменит рабочую (или уйдёт на Диск). */
export function validateDatabase(deps: SyncDeps, path: string): void {
  let db: Db;
  try {
    db = deps.openDb(path);
  } catch (e) {
    throw new SyncError(`Файл базы не открывается: ${(e as Error).message}`);
  }
  try {
    db.exec('PRAGMA trusted_schema = OFF; PRAGMA query_only = ON;');
    if (db.get<{ quick_check: string }>('PRAGMA quick_check')?.quick_check !== 'ok') {
      throw new SyncError('Файл базы повреждён.');
    }
    if (db.get<{ user_version: number }>('PRAGMA user_version')!.user_version > SCHEMA_VERSION) {
      throw new SyncError('База создана более новой версией программы. Обновите приложение на этом устройстве.');
    }
    for (const { type, name } of db.all<{ type: string; name: string }>('SELECT type, name FROM sqlite_master')) {
      if (type === 'trigger' || type === 'view' || (type === 'table' && !ALLOWED_TABLES.has(name))) {
        throw new SyncError('База содержит посторонние объекты и не может быть использована.');
      }
    }
  } catch (e) {
    if (e instanceof SyncError) throw e;
    throw new SyncError(`Файл не является базой программы: ${(e as Error).message}`);
  } finally {
    db.close();
  }
}

/** gunzip с пределом размера распакованного: защита от gzip-бомбы. */
function unpack(packed: Uint8Array, maxBytes: number): Uint8Array {
  const chunks: Uint8Array[] = [];
  let total = 0;
  let tooBig = false;
  const gunzip = new Gunzip((chunk) => {
    total += chunk.length;
    if (total > maxBytes) tooBig = true;
    else chunks.push(chunk);
  });
  try {
    const step = 64 * 1024;
    for (let i = 0; i < packed.length && !tooBig; i += step) {
      gunzip.push(packed.subarray(i, i + step), i + step >= packed.length);
    }
  } catch {
    throw new SyncError('Файл базы на Диске повреждён.');
  }
  if (tooBig) throw new SyncError('Файл базы на Диске слишком большой и не может быть загружен.');
  if (packed.length === 0) throw new SyncError('Файл базы на Диске повреждён.');
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

export interface SyncOptions {
  dbPath: string;
  client: WebDavClient;
  remoteDir: string;
  statePath: string;
  deviceName: string;
  /** К чему относится сохранённое состояние (сервер, аккаунт, папка). */
  targetId?: string;
  /** Передача файла базы идёт дольше служебных запросов. */
  transferTimeoutMs?: number;
  /** Предел распакованной базы; меньше — только в тестах. */
  maxDbBytes?: number;
}

export class SyncService {
  readonly dbPath: string;
  private readonly client: WebDavClient;
  private readonly remoteDir: string;
  private readonly statePath: string;
  private readonly deviceName: string;
  private readonly targetId: string;
  private readonly transferTimeoutMs?: number;
  private readonly maxDbBytes: number;
  private rollback: { backup: string | null; state: State } | null = null;

  constructor(private readonly deps: SyncDeps, opts: SyncOptions) {
    this.dbPath = opts.dbPath;
    this.client = opts.client;
    this.remoteDir = opts.remoteDir.replace(/^\/+|\/+$/g, '');
    this.statePath = opts.statePath;
    this.deviceName = opts.deviceName;
    this.targetId = opts.targetId ?? '';
    this.transferTimeoutMs = opts.transferTimeoutMs;
    this.maxDbBytes = opts.maxDbBytes ?? MAX_DB_BYTES;
  }

  // --- состояние ---

  private async loadState(): Promise<State> {
    const empty: State = { base_rev: 0, base_sha256: '', synced_at: '' };
    try {
      const state = JSON.parse(await this.deps.files.readText(this.statePath));
      if ((state.target ?? '') !== this.targetId) return empty;
      const rev = Number.parseInt(String(state.base_rev), 10);
      if (!Number.isFinite(rev) || state.base_sha256 === undefined) return empty;
      return { base_rev: rev, base_sha256: String(state.base_sha256), synced_at: validTime(state.synced_at) };
    } catch {
      return empty; // файла нет или повреждён — ещё не синхронизировались
    }
  }

  private async saveState(rev: number, sha256: string, syncedAt?: string): Promise<void> {
    const tmp = `${this.statePath}.${Date.now()}.${Math.random().toString(36).slice(2)}.tmp`;
    const body = JSON.stringify({
      base_rev: rev, base_sha256: sha256,
      synced_at: syncedAt ?? savedAtNow(this.deps.clock), target: this.targetId,
    });
    try {
      await this.deps.files.write(tmp, body);
      await this.deps.files.replace(tmp, this.statePath);
    } finally {
      if (await this.deps.files.exists(tmp)) await this.deps.files.remove(tmp);
    }
  }

  /** Сбой записи состояния — не сбой обмена: check() восстановит его по содержимому. */
  private async remember(rev: number, sha256: string): Promise<void> {
    try {
      await this.saveState(rev, sha256);
    } catch {
      // см. выше
    }
  }

  private async readLocal(): Promise<Uint8Array> {
    // Оставшийся журнал — незавершённая запись: файл без него несогласован.
    if (await this.deps.files.exists(`${this.dbPath}-journal`)) {
      throw new SyncError('База занята незавершённой записью. Перезапустите приложение.');
    }
    return this.deps.files.read(this.dbPath);
  }

  private async localSha(): Promise<string> {
    if (!(await this.deps.files.exists(this.dbPath))) return '';
    return this.deps.sha256(await this.readLocal());
  }

  /** В базе нет ни клиентов, ни импортов — терять на этом устройстве нечего. */
  private async localIsEmpty(): Promise<boolean> {
    if (!(await this.deps.files.exists(this.dbPath))) return true;
    let db: Db;
    try {
      db = this.deps.openDb(this.dbPath);
    } catch (e) {
      throw new SyncError(`Файл базы на этом устройстве повреждён: ${(e as Error).message}`);
    }
    try {
      for (const table of ['clients', 'import_sessions', 'shipments']) {
        const exists = db.get("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", [table]);
        if (exists && db.get(`SELECT 1 FROM ${table} LIMIT 1`)) return false;
      }
      return true;
    } catch (e) {
      throw new SyncError(`Файл базы на этом устройстве повреждён: ${(e as Error).message}`);
    } finally {
      db.close();
    }
  }

  private async localDiffersFromBase(localSha: string, baseSha: string): Promise<boolean> {
    return localSha !== baseSha && !(await this.localIsEmpty());
  }

  async remoteMeta(): Promise<RemoteMeta | null> {
    const raw = await this.client.get(`${this.remoteDir}/${META_NAME}`, { maxBytes: MAX_META_BYTES });
    if (raw === null) return null;
    try {
      return parseMeta(raw);
    } catch {
      throw new SyncError(`Файл ${META_NAME} на Диске повреждён.`);
    }
  }

  // --- операции ---

  /** Есть ли невыложенные правки. Без сети. */
  async localChanged(): Promise<boolean> {
    return this.localDiffersFromBase(await this.localSha(), (await this.loadState()).base_sha256);
  }

  async check(): Promise<SyncCheck> {
    const meta = await this.remoteMeta();
    const state = await this.loadState();
    const localSha = await this.localSha();
    const localChanged = await this.localDiffersFromBase(localSha, state.base_sha256);
    const result = (status: SyncStatus, m: RemoteMeta | null, remoteIsOlder = false): SyncCheck =>
      ({ status, meta: m, remoteIsOlder, localSyncedAt: state.synced_at, neverSynced: state.base_rev === 0 });

    if (meta === null) {
      if (state.base_rev > 0 && !(await this.localIsEmpty())) {
        return { ...result(SyncStatus.REMOTE_MISSING, null), localSyncedAt: '' };
      }
      return { ...result(localChanged ? SyncStatus.LOCAL_AHEAD : SyncStatus.UP_TO_DATE, null), localSyncedAt: '' };
    }
    if (localSha === meta.sha256) {
      if (state.base_rev !== meta.rev || state.base_sha256 !== meta.sha256) {
        await this.remember(meta.rev, meta.sha256);
      }
      return { ...result(SyncStatus.UP_TO_DATE, meta), localSyncedAt: '' };
    }
    // Сравниваем и содержимое: номер ревизии мог достаться другой базе.
    const remoteChanged = meta.rev !== state.base_rev || meta.sha256 !== state.base_sha256;
    let status: SyncStatus;
    if (remoteChanged && localChanged) status = SyncStatus.CONFLICT;
    // Вторая ветка — пустая локальная база при непустом Диске: её заменяем.
    else if (remoteChanged || !localChanged) status = SyncStatus.REMOTE_AHEAD;
    else status = SyncStatus.LOCAL_AHEAD;
    const rewound = meta.rev < state.base_rev
      || (state.base_rev > 0 && meta.rev === state.base_rev && meta.sha256 !== state.base_sha256);
    return result(status, meta, rewound);
  }

  /**
   * Выложить локальную базу новой ревизией. expected — ревизия Диска, по
   * которой принималось решение; если Диск изменился — отказ.
   * Соединения с базой должны быть закрыты.
   */
  async push(expected: RemoteMeta | null, token: CancelToken = new CancelToken()): Promise<number> {
    const raw = await this.readLocal();
    const sha = await this.deps.sha256(raw);
    validateDatabase(this.deps, this.dbPath); // повреждённую не выкладываем
    const state = await this.loadState();
    if (expected !== null) {
      // Поверх базы на Диске — только базу, которая от неё происходит. Пустая база
      // или база устройства, ни разу не загружавшего Диск, стёрла бы данные.
      if (await this.localIsEmpty()) throw new SyncError(EMPTY_BASE_ERROR);
      if (state.base_rev === 0) throw new SyncError(NEVER_SYNCED_ERROR);
    }
    const db = this.deps.openDb(this.dbPath);
    let schemaVersion: number;
    try {
      schemaVersion = db.get<{ user_version: number }>('PRAGMA user_version')!.user_version;
    } finally {
      db.close();
    }
    // От максимума: после отката Диска номер не должен стать меньше виденного.
    const rev = Math.max(expected?.rev ?? 0, state.base_rev) + 1;
    if (rev > MAX_REV) throw new SyncError('Исчерпаны номера ревизий на Диске.');
    const name = `ozon_sorter_rev${String(rev).padStart(6, '0')}_${sha.slice(0, 8)}.db.gz`;

    await this.client.ensureDir(this.remoteDir);
    // Сначала данные, потом meta.json: оборванная выгрузка не ломает текущую ревизию.
    const packed = gzipSync(raw, { level: 6 });
    await this.client.put(`${this.remoteDir}/${name}`, packed, this.transferTimeoutMs);
    token.commit();
    if (!sameRevision(await this.remoteMeta(), expected)) {
      throw new SyncError('Пока шла выгрузка, базу на Диске изменило другое устройство. Повторите синхронизацию.');
    }
    const meta: RemoteMeta = {
      rev, file: name, sha256: sha, schema_version: schemaVersion,
      device: this.deviceName, saved_at: savedAtNow(this.deps.clock),
    };
    await this.client.put(`${this.remoteDir}/${META_NAME}`, new TextEncoder().encode(dumpMeta(meta)));
    await this.remember(rev, sha);
    await this.dailyBackup(packed);
    await this.pruneRemote(rev);
    return rev;
  }

  /** Первая выгрузка за день кладёт копию в backups/; сбой — не сбой выгрузки. */
  private async dailyBackup(packed: Uint8Array): Promise<void> {
    const folder = `${this.remoteDir}/${BACKUP_DIR}`;
    const now = this.deps.clock.now();
    const p = (v: number, w: number) => String(v).padStart(w, '0');
    const name = `ozon_sorter_${p(now.year, 4)}-${p(now.month, 2)}-${p(now.day, 2)}.db.gz`;
    try {
      const existing = (await this.client.listDir(folder)).filter((n) => DAILY_RE.test(n)).sort();
      if (!existing.includes(name)) {
        await this.client.ensureDir(folder);
        await this.client.put(`${folder}/${name}`, packed, this.transferTimeoutMs);
        existing.push(name);
        existing.sort();
      }
      for (const old of existing.slice(0, Math.max(0, existing.length - KEEP_DAILY_BACKUPS))) {
        await this.client.delete(`${folder}/${old}`);
      }
    } catch (e) {
      if (!(e instanceof WebDavError)) throw e;
      // выгрузка уже состоялась; копию сделает следующая
    }
  }

  private async pruneRemote(currentRev: number): Promise<void> {
    try {
      for (const name of await this.client.listDir(this.remoteDir)) {
        const m = REVISION_RE.exec(name);
        if (m && Number(m[1]) <= currentRev - KEEP_REMOTE_REVISIONS) {
          await this.client.delete(`${this.remoteDir}/${name}`);
        }
      }
    } catch (e) {
      if (!(e instanceof WebDavError)) throw e;
      // выгрузка уже состоялась; лишнее уберёт следующая
    }
  }

  /**
   * Заменить локальную базу версией с Диска. expected — ревизия, которую
   * пользователь согласился загрузить. Прежний файл сохраняется в
   * backups/presync. Соединения с базой должны быть закрыты.
   */
  async pull(expected: RemoteMeta | null = null, token: CancelToken = new CancelToken()): Promise<number> {
    const meta = await this.remoteMeta();
    if (meta === null) throw new SyncError('На Диске ещё нет базы.');
    if (expected !== null && !sameRevision(meta, expected)) {
      throw new SyncError('База на Диске только что изменилась. Повторите синхронизацию.');
    }
    if (meta.schema_version > SCHEMA_VERSION) {
      throw new SyncError('База на Диске создана более новой версией программы. Обновите приложение на этом устройстве.');
    }
    const packed = await this.client.get(`${this.remoteDir}/${meta.file}`, {
      timeoutMs: this.transferTimeoutMs, maxBytes: this.maxDbBytes,
    });
    if (packed === null) throw new SyncError(`На Диске нет файла базы, на который ссылается ${META_NAME}.`);
    const raw = unpack(packed, this.maxDbBytes);
    // Сумма лежит рядом с файлом и защищает только от порчи при передаче.
    if ((await this.deps.sha256(raw)) !== meta.sha256) {
      throw new SyncError('Файл базы на Диске не совпадает с контрольной суммой. Повторите позже.');
    }

    const tmp = `${this.dbPath}.sync-tmp`;
    try {
      await this.deps.files.write(tmp, raw);
      validateDatabase(this.deps, tmp);
      token.commit();
      this.rollback = { backup: await this.backupLocal(), state: await this.loadState() };
      await this.deps.files.replace(tmp, this.dbPath);
    } finally {
      if (await this.deps.files.exists(tmp)) await this.deps.files.remove(tmp);
    }
    await this.remember(meta.rev, meta.sha256);
    return meta.rev;
  }

  /** Вернуть базу, которая была до последнего pull() (если загруженная не открылась). */
  async rollbackPull(): Promise<void> {
    if (this.rollback === null) return;
    const { backup, state } = this.rollback;
    if (backup === null) {
      if (await this.deps.files.exists(this.dbPath)) await this.deps.files.remove(this.dbPath);
    } else {
      const tmp = `${this.dbPath}.sync-tmp`;
      try {
        await this.deps.files.copy(backup, tmp);
        await this.deps.files.replace(tmp, this.dbPath);
      } finally {
        if (await this.deps.files.exists(tmp)) await this.deps.files.remove(tmp);
      }
    }
    // Только после успешной замены: при сбое откат можно повторить.
    this.rollback = null;
    await this.saveState(state.base_rev, state.base_sha256, state.synced_at);
  }

  /** Копия текущей базы перед заменой; null — базы нет. */
  private async backupLocal(): Promise<string | null> {
    const { files, clock } = this.deps;
    if (!(await files.exists(this.dbPath))) return null;
    // Отдельная папка: общая ротация копий не вытеснит сделанные перед заменой.
    const dir = files.join(files.join(files.dirname(this.dbPath), 'backups'), 'presync');
    await files.mkdirs(dir);
    const backup = files.join(dir, `ozon_sorter_${stamp(clock.now())}.db.bak`);
    await files.copy(this.dbPath, backup);
    const all = (await files.list(dir)).sort();
    for (const old of all.slice(0, Math.max(0, all.length - KEEP_PRESYNC_BACKUPS))) {
      await files.remove(files.join(dir, old));
    }
    return backup;
  }
}

/** strftime("%Y%m%d_%H%M%S_%f") */
function stamp(t: PyDateTime): string {
  const p = (n: number, w: number) => String(n).padStart(w, '0');
  return `${p(t.year, 4)}${p(t.month, 2)}${p(t.day, 2)}_${p(t.hour, 2)}${p(t.minute, 2)}${p(t.second, 2)}_${p(t.microsecond, 6)}`;
}
