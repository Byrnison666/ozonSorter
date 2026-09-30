/**
 * Синхронный доступ к SQLite, общий для телефона (expo-sqlite) и тестов (bun:sqlite).
 * Логика приложения зависит только от этого интерфейса.
 */
export type SqlValue = string | number | null;

export interface RunResult {
  changes: number;
  lastInsertRowId: number;
}

export interface Db {
  /** Несколько операторов без параметров (DDL, PRAGMA). */
  exec(sql: string): void;
  run(sql: string, params?: SqlValue[]): RunResult;
  all<T>(sql: string, params?: SqlValue[]): T[];
  get<T>(sql: string, params?: SqlValue[]): T | undefined;
  /** Выполнить fn в транзакции; исключение откатывает её и пробрасывается. */
  transaction(fn: () => void): void;
  close(): void;
}
