import { defaultDatabaseDirectory, openDatabaseSync, type SQLiteDatabase } from 'expo-sqlite';

import type { Db, RunResult, SqlValue } from '../core/db';

class ExpoDb implements Db {
  constructor(private readonly db: SQLiteDatabase) {}

  exec(sql: string): void {
    this.db.execSync(sql);
  }

  run(sql: string, params: SqlValue[] = []): RunResult {
    const { changes, lastInsertRowId } = this.db.runSync(sql, params);
    return { changes, lastInsertRowId };
  }

  all<T>(sql: string, params: SqlValue[] = []): T[] {
    return this.db.getAllSync<T>(sql, params);
  }

  get<T>(sql: string, params: SqlValue[] = []): T | undefined {
    return this.db.getFirstSync<T>(sql, params) ?? undefined;
  }

  transaction(fn: () => void): void {
    this.db.withTransactionSync(fn);
  }

  close(): void {
    this.db.closeSync();
  }
}

/** Каталог данных приложения: база, копии, состояние синхронизации. */
export const DATA_DIR: string = defaultDatabaseDirectory;

/**
 * Открыть файл SQLite по абсолютному пути. Отдельное соединение: синхронизация
 * проверяет и читает файлы, пока основное закрыто.
 */
export function openExpoDb(path: string): Db {
  const slash = path.lastIndexOf('/');
  return new ExpoDb(openDatabaseSync(path.slice(slash + 1), { useNewConnection: true }, path.slice(0, slash)));
}
