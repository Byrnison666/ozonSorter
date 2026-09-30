import { afterEach, beforeEach, describe, expect, test } from 'bun:test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { configureConnection, ensureSchema, SCHEMA_VERSION, SchemaError } from '../src/core/schema';
import { openBunDb } from './support/bunDb';
import { createPcDatabase, runPython, tempDir } from './support/pc';

type MasterRow = { type: string; name: string; tbl_name: string; sql: string | null };

function master(path: string): MasterRow[] {
  const db = openBunDb(path);
  try {
    return db.all<MasterRow>('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name');
  } finally {
    db.close();
  }
}

function userVersion(path: string): number {
  const db = openBunDb(path);
  try {
    return db.get<{ user_version: number }>('PRAGMA user_version')!.user_version;
  } finally {
    db.close();
  }
}

function sha(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

function openPhone(path: string) {
  const db = openBunDb(path);
  configureConnection(db);
  ensureSchema(db);
  return db;
}

describe('схема базы телефона', () => {
  let dir: ReturnType<typeof tempDir>;
  beforeEach(() => {
    dir = tempDir('ozon-schema-');
  });
  afterEach(() => dir.cleanup());

  test('новая база совпадает со свежей базой ПК', () => {
    const pc = join(dir.path, 'pc.db');
    const phone = join(dir.path, 'phone.db');
    createPcDatabase(pc);
    openPhone(phone).close();

    expect(master(phone)).toEqual(master(pc));
    expect(userVersion(phone)).toBe(userVersion(pc));
    expect(userVersion(phone)).toBe(SCHEMA_VERSION);
  });

  test('версия схемы совпадает с ПК', () => {
    const pcVersion = Number(runPython('from src.database import SCHEMA_VERSION\nprint(SCHEMA_VERSION)'));
    expect(SCHEMA_VERSION).toBe(pcVersion);
  });

  test('открытие базы с ПК не меняет файл', () => {
    const pc = join(dir.path, 'pc.db');
    createPcDatabase(pc);
    const before = sha(pc);
    openPhone(pc).close();
    expect(sha(pc)).toBe(before);
  });

  test('журнал DELETE: рядом с базой не остаётся -wal', () => {
    const phone = join(dir.path, 'phone.db');
    const db = openPhone(phone);
    expect(db.get<{ journal_mode: string }>('PRAGMA journal_mode')!.journal_mode).toBe('delete');
    db.close();
  });

  test('база ПК открывается в Python после создания на телефоне', () => {
    const phone = join(dir.path, 'phone.db');
    openPhone(phone).close();
    const before = sha(phone);
    createPcDatabase(phone); // create_tables + _migrate поверх базы телефона
    expect(sha(phone)).toBe(before); // миграциям ПК нечего менять
  });

  test('база более новой версии отвергается', () => {
    const phone = join(dir.path, 'phone.db');
    openPhone(phone).close();
    const db = openBunDb(phone);
    db.exec(`PRAGMA user_version = ${SCHEMA_VERSION + 1}`);
    db.close();
    expect(() => openPhone(phone)).toThrow(SchemaError);
  });
});
