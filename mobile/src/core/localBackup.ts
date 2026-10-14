/**
 * Копии базы на самом телефоне — на случай, когда Яндекс.Диск недоступен или
 * отдаёт испорченные данные (сбои в дата-центрах Яндекса): ежедневная копия
 * в backups/daily, список копий для экрана «Резервные копии» и восстановление
 * базы из копии или выбранного файла.
 *
 * Тексты ошибок — для пользователя, как у проверок синхронизации (SyncError).
 */
import type { Db } from './db';
import { missingColumns } from './schema';
import { type DbHost, formatTime } from './sync/flow';
import {
  backupBeforeReplace, databaseHasData, type LocalFiles, presyncDir, SyncError, type SyncDeps, validateDatabase,
} from './sync/syncService';

export const KEEP_DAILY_LOCAL = 30;
const DAILY_RE = /^ozon_sorter_(\d{4})-(\d{2})-(\d{2})\.db$/;
const PRESYNC_RE = /^ozon_sorter_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})_\d{6}\.db\.bak$/;
/** Таблицы программы: без них файл — не база OzonSorter. */
const APP_TABLES = ['clients', 'import_sessions', 'shipments', 'export_sessions'];

type Deps = Pick<SyncDeps, 'files' | 'openDb' | 'clock'>;

export function dailyDir(files: LocalFiles, dbPath: string): string {
  return files.join(files.join(files.dirname(dbPath), 'backups'), 'daily');
}

async function removeQuietly(files: LocalFiles, path: string): Promise<void> {
  try {
    await files.remove(path);
  } catch (e) {
    console.warn('Temporary file was not removed', path, e);
  }
}

/** Файл — целая база программы с данными и схемой не новее этой версии; иначе SyncError. */
export function checkBackupFile(deps: Pick<SyncDeps, 'openDb'>, path: string): void {
  validateDatabase(deps, path); // целостность, версия схемы, посторонние объекты
  const db = deps.openDb(path);
  try {
    db.exec('PRAGMA trusted_schema = OFF; PRAGMA query_only = ON;');
    const tables = new Set(
      db.all<{ name: string }>("SELECT name FROM sqlite_master WHERE type = 'table'").map((r) => r.name),
    );
    if (!APP_TABLES.every((t) => tables.has(t))) {
      throw new SyncError('Файл не является базой программы: в нём нет таблиц приложения.');
    }
    // Телефон схему не мигрирует (это делает ПК при запуске), а при открытии
    // проверяет только версию: несовместимую базу он открыл бы и падал бы на
    // каждом экране. Версия в базе появилась в ПК 1.8.0; база без неё могла не
    // пройти миграции (ключ посылок, слияние клиентов), а не только без колонок.
    if (db.get<{ user_version: number }>('PRAGMA user_version')!.user_version < 1) {
      throw new SyncError(
        'Копия сделана программой до версии 1.8.0, телефон такую базу не обновит. Восстановите её на компьютере.',
      );
    }
    if (missingColumns(db).length) {
      throw new SyncError('Файл не является базой программы: в таблицах не хватает колонок.');
    }
    if (!databaseHasData(db)) throw new SyncError('В файле пустая база — восстанавливать из неё нечего.');
  } finally {
    db.close();
  }
}

/**
 * Ежедневная копия базы, первая за день, — независимо от Диска.
 *
 * VACUUM INTO из открытого соединения даёт согласованный снимок без закрытия
 * базы; рабочий файл при этом только читается, и синхронизация не видит
 * правки. Копия получает своё имя только после проверки, старые удаляются
 * после этого: сбой не вытесняет целые копии. Сбой — только предупреждение в
 * журнале. db — открытая база (бросает, если она сейчас закрыта).
 * Возвращает путь новой копии; null — копия за сегодня уже есть, база пуста
 * или копия не удалась.
 */
export async function dailyBackup(deps: Deps, dbPath: string, db: () => Db): Promise<string | null> {
  const { files } = deps;
  const dir = dailyDir(files, dbPath);
  const now = deps.clock.now();
  const p = (v: number, w: number) => String(v).padStart(w, '0');
  const target = files.join(dir, `ozon_sorter_${p(now.year, 4)}-${p(now.month, 2)}-${p(now.day, 2)}.db`);
  const tmp = `${target}.tmp`;
  try {
    if (await files.exists(target)) return null;
    await files.mkdirs(dir);
    // Остатки оборванных попыток: VACUUM INTO не пишет в существующий файл.
    for (const name of await files.list(dir)) {
      if (name.endsWith('.tmp')) await files.remove(files.join(dir, name));
    }
    const conn = db();
    // Из пустой базы восстанавливать нечего — такие копии только вытеснили бы настоящие.
    if (!databaseHasData(conn)) return null;
    conn.run('VACUUM INTO ?', [tmp]);
    checkBackupFile(deps, tmp);
    await files.replace(tmp, target);
  } catch (e) {
    console.warn('Daily database backup failed, previous copies are kept', e);
    return null;
  } finally {
    await removeQuietly(files, tmp);
  }
  try {
    const copies = (await files.list(dir)).filter((n) => DAILY_RE.test(n)).sort();
    for (const old of copies.slice(0, Math.max(0, copies.length - KEEP_DAILY_LOCAL))) {
      await files.remove(files.join(dir, old));
    }
  } catch (e) {
    console.warn('Old daily backups were not removed', e);
  }
  return target;
}

export interface LocalBackup {
  path: string;
  name: string;
  /** daily — копия дня; presync — база перед заменой (загрузка с Диска, восстановление). */
  kind: 'daily' | 'presync';
  /** '14.10.2026' или '14.10.2026 15:30' */
  when: string;
  size: number;
  sortKey: string;
}

/** Копии на телефоне, новые первыми. */
export async function listBackups(files: LocalFiles, dbPath: string): Promise<LocalBackup[]> {
  const out: LocalBackup[] = [];
  const sources = [
    ['daily', dailyDir(files, dbPath), DAILY_RE],
    ['presync', presyncDir(files, dbPath), PRESYNC_RE],
  ] as const;
  for (const [kind, dir, re] of sources) {
    if (!(await files.exists(dir))) continue;
    for (const name of await files.list(dir)) {
      const m = re.exec(name);
      if (!m) continue;
      const [y, mo, d, h, mi, s] = m.slice(1);
      const path = files.join(dir, name);
      out.push({
        path, name, kind,
        when: h === undefined ? `${d}.${mo}.${y}` : `${d}.${mo}.${y} ${h}:${mi}`,
        size: await files.size(path),
        sortKey: `${y}${mo}${d}${h ?? ''}${mi ?? ''}${s ?? ''}`,
      });
    }
  }
  return out.sort((a, b) => b.sortKey.localeCompare(a.sortKey));
}

export interface BackupSummary {
  clients: number;
  /** Начало последнего импорта ('2026-10-13 18:20:00…'); '' — импортов нет. */
  lastImport: string;
}

/** Проверить файл (как checkBackupFile) и коротко описать, что в нём, — для подтверждения. */
export function inspectBackup(deps: Pick<SyncDeps, 'openDb'>, path: string): BackupSummary {
  checkBackupFile(deps, path);
  const db = deps.openDb(path);
  try {
    db.exec('PRAGMA trusted_schema = OFF; PRAGMA query_only = ON;');
    const clients = db.get<{ n: number }>('SELECT count(*) AS n FROM clients')!.n;
    const last = db.get<{ t: string | null }>('SELECT max(started_at) AS t FROM import_sessions')?.t;
    return { clients, lastImport: typeof last === 'string' ? last : '' };
  } finally {
    db.close();
  }
}

/** Текст подтверждения: что за копия и что будет заменено. */
export function restoreQuestion(source: string, summary: BackupSummary): string {
  const content = `В копии: клиентов ${summary.clients}, последний импорт ${formatTime(summary.lastImport)}.`;
  return `Восстановить базу из ${source}?\n${content}\n\n` +
    'База на этом телефоне будет заменена этой копией, а при следующей синхронизации копия заменит ' +
    'и базу на Яндекс.Диске. Текущая база телефона сохранится в резервных копиях.';
}

/**
 * Заменить рабочую базу копией из списка или выбранным файлом — так же, как
 * загрузка с Диска: проверить, сохранить текущую в backups/presync, заменить,
 * открыть; не открылась — вернуть прежнюю. Состояние синхронизации не
 * трогаем: база отличается от выложенной, следующая синхронизация выложит её
 * (или спросит, если базу на Диске тоже меняли).
 */
export async function restoreDatabase(deps: Deps, dbPath: string, source: string, host: DbHost): Promise<void> {
  const { files } = deps;
  const tmp = `${dbPath}.restore-tmp`;
  try {
    // Проверяем ровно тот файл, который встанет на место базы.
    await files.copy(source, tmp);
    checkBackupFile(deps, tmp);
    host.release();
    let backup: string | null;
    try {
      if (await files.exists(`${dbPath}-journal`)) {
        throw new SyncError('База занята незавершённой записью. Перезапустите приложение.');
      }
      backup = await backupBeforeReplace(deps, dbPath);
      await files.replace(tmp, dbPath);
    } catch (e) {
      reopen(host);
      throw e;
    }
    try {
      host.reload();
    } catch (e) {
      host.release();
      try {
        await putBack(files, dbPath, backup, tmp);
      } catch (backErr) {
        console.warn('Previous database was not put back', backErr);
        throw new SyncError(
          'Копия не открылась, и прежнюю базу вернуть не удалось. Её копия — в списке резервных копий ' +
            '(«перед заменой»).',
        );
      }
      reopen(host);
      throw new SyncError(`Копия не открылась в приложении, возвращена прежняя база. (${(e as Error).message})`);
    }
  } finally {
    await removeQuietly(files, tmp);
  }
}

/** Открыть базу как была; ошибку покажет экран (база не открылась). */
function reopen(host: DbHost): void {
  try {
    host.reload();
  } catch (e) {
    console.warn('Database reopen failed', e);
  }
}

async function putBack(files: LocalFiles, dbPath: string, backup: string | null, tmp: string): Promise<void> {
  if (backup === null) {
    await files.remove(dbPath);
    return;
  }
  await files.copy(backup, tmp);
  await files.replace(tmp, dbPath);
}
