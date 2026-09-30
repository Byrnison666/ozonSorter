import { openDatabaseSync, type SQLiteDatabase } from 'expo-sqlite';

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

/** directory — папка файла базы (по умолчанию каталог SQLite приложения). */
export function openExpoDb(name: string, directory?: string): Db {
  return new ExpoDb(openDatabaseSync(name, undefined, directory));
}
