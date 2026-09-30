import { Database } from 'bun:sqlite';

import type { Db, RunResult, SqlValue } from '../../src/core/db';

class BunDb implements Db {
  constructor(private readonly db: Database) {}

  exec(sql: string): void {
    this.db.exec(sql);
  }

  run(sql: string, params: SqlValue[] = []): RunResult {
    const { changes, lastInsertRowid } = this.db.query(sql).run(...params);
    return { changes, lastInsertRowId: Number(lastInsertRowid) };
  }

  all<T>(sql: string, params: SqlValue[] = []): T[] {
    return this.db.query(sql).all(...params) as T[];
  }

  get<T>(sql: string, params: SqlValue[] = []): T | undefined {
    return (this.db.query(sql).get(...params) as T | null) ?? undefined;
  }

  transaction(fn: () => void): void {
    this.db.transaction(fn)();
  }

  close(): void {
    this.db.close();
  }
}

export function openBunDb(path: string): Db {
  return new BunDb(new Database(path));
}
