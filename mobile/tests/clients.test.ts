import { afterAll, beforeAll, describe, expect, test } from 'bun:test';
import { copyFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { importClients } from '../src/core/clientImport';
import {
  addClient, deactivateClient, editClient, type ClientForm, type ClientResult,
} from '../src/core/clients';
import type { Db } from '../src/core/db';
import type { DeliveryPoint } from '../src/core/models';
import { configureConnection, ensureSchema } from '../src/core/schema';
import { readActiveSheet } from '../src/core/sheet';
import { openBunDb } from './support/bunDb';
import { dumpDb, StepClock } from './support/dump';
import { runPythonScript, tempDir } from './support/pc';

type PcData = { ozon_client_id: string; full_name: string; phone: string; point: string | null };

function form(d: PcData): ClientForm {
  return {
    ozonClientId: d.ozon_client_id, fullName: d.full_name, phone: d.phone,
    point: d.point as DeliveryPoint | null,
  };
}

function messages(r: ClientResult): string[] {
  return r.ok ? [] : [r.error];
}

describe('клиенты совпадают с ПК', () => {
  const dir = tempDir('ozon-clients-');
  const pcDb = join(dir.path, 'pc.db');
  const phoneDb = join(dir.path, 'phone.db');
  const clock = new StepClock();
  const dump = (p: string) => dumpDb(p, clock);

  function withPhone<T>(fn: (db: Db) => T): T {
    const db = openBunDb(phoneDb);
    configureConnection(db);
    ensureSchema(db);
    try {
      return fn(db);
    } finally {
      db.close();
    }
  }

  function pcOp(op: object) {
    return JSON.parse(runPythonScript('clients_op.py', pcDb, JSON.stringify(op)));
  }

  function idOf(db: Db, ozon: string): number {
    return db.get<{ id: number }>('SELECT id FROM clients WHERE ozon_client_id = ?', [ozon])!.id;
  }

  beforeAll(() => {
    runPythonScript('clients_fixtures.py', dir.path);
    copyFileSync(join(dir.path, 'base.db'), pcDb);
    copyFileSync(join(dir.path, 'base.db'), phoneDb);
  });
  afterAll(() => dir.cleanup());

  test('импорт клиентов из файла', () => {
    const file = join(dir.path, 'clients.xlsx');
    clock.run('import', () => {
      const pc = pcOp({ op: 'import', file });
      const phone = withPhone((db) => importClients(db, readActiveSheet(new Uint8Array(readFileSync(file)))));
      expect(phone).toEqual(pc.result);
      expect(pc.result.errors.length).toBeGreaterThanOrEqual(4);
    });
    expect(dump(phoneDb)).toEqual(dump(pcDb));
  });

  const K4 = 'KOMSOMOLSKAYA_4';
  const K16 = 'KOLTSEVAYA_16';
  const steps: Array<[string, { op: 'add' | 'edit' | 'delete'; target?: string; data?: PcData }]> = [
    ['add-duplicate-normalized', { op: 'add', data: { ozon_client_id: '0224933356', full_name: '', phone: '', point: K4 } }],
    ['add-bad-id', { op: 'add', data: { ozon_client_id: '12a', full_name: '', phone: '', point: K4 } }],
    ['add-no-point', { op: 'add', data: { ozon_client_id: '400000099', full_name: '', phone: '', point: null } }],
    ['add-ok', { op: 'add', data: { ozon_client_id: '0400000099', full_name: 'Новый', phone: '', point: K16 } }],
    ['edit-change', { op: 'edit', target: '900000001', data: { ozon_client_id: '900000001', full_name: 'Изменён', phone: '+7 1', point: K16 } }],
    ['edit-same', { op: 'edit', target: '900000001', data: { ozon_client_id: '0900000001', full_name: 'Изменён', phone: '+7 1', point: K16 } }],
    ['edit-clash', { op: 'edit', target: '900000001', data: { ozon_client_id: '224933356', full_name: 'X', phone: '', point: K4 } }],
    ['delete', { op: 'delete', target: '900000002' }],
    ['delete-again', { op: 'delete', target: '900000002' }],
    ['add-deleted-again', { op: 'add', data: { ozon_client_id: '900000002', full_name: 'Снова', phone: '', point: K4 } }],
  ];

  test.each(steps)('%s', (step, op) => {
    clock.run(step, () => {
      const pc = pcOp(op);
      const phone = withPhone((db) => {
        if (op.op === 'add') return messages(addClient(db, form(op.data!)));
        const id = idOf(db, op.target!);
        if (op.op === 'edit') return messages(editClient(db, id, form(op.data!)));
        deactivateClient(db, id);
        return [];
      });
      expect(phone).toEqual(pc.messages);
    });
    expect(dump(phoneDb)).toEqual(dump(pcDb));
  });

  test('правка без изменений не трогает updated_at', () => {
    const clients = dump(pcDb).clients;
    const edited = clients.find((c) => c.ozon_client_id[1] === '900000001')!;
    expect(edited.updated_at[1]).toBe('@edit-change');
    const untouched = clients.find((c) => c.ozon_client_id[1] === '777000222')!;
    expect(untouched.updated_at[1]).toBe('2026-09-01 10:00:00.000000');
  });
});
