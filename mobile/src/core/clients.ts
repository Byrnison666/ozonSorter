/**
 * Клиенты: ручное добавление, изменение, удаление — операции с базой экрана
 * «Клиенты» ПК (src/ui/clients_screen.py). updated_at меняется только когда
 * запись действительно изменилась: так делает onupdate SQLAlchemy.
 */
import type { Db } from './db';
import { type Clock, type DeliveryPoint, systemClock } from './models';
import { normalizeOzonId } from './parser';
import { pyStrip } from './py';

export interface ClientRow {
  id: number;
  ozon_client_id: string;
  full_name: string | null;
  phone: string | null;
  fixed_delivery_point: string | null;
  notes: string | null;
  is_active: number;
  created_at: string;
  updated_at: string;
}

/** Данные формы клиента (строки как ввёл оператор). */
export interface ClientForm {
  ozonClientId: string;
  fullName: string;
  phone: string;
  point: DeliveryPoint | null;
}

export type ClientResult = { ok: true; id: number } | { ok: false; error: string };

// str.isdigit(): десятичные цифры Unicode. Надстрочные «²» Python тоже считает
// цифрами — здесь они отвергаются (строже ПК, не мягче).
const DIGITS_RE = /^\p{Nd}+$/u;

export function isDigits(s: string): boolean {
  return DIGITS_RE.test(s);
}

function clean(form: ClientForm): ClientForm {
  return {
    ozonClientId: pyStrip(form.ozonClientId),
    fullName: pyStrip(form.fullName),
    phone: pyStrip(form.phone),
    point: form.point,
  };
}

/** _validate экрана клиентов; null — данные в порядке. */
export function validateClient(form: ClientForm): string | null {
  const data = clean(form);
  if (!isDigits(data.ozonClientId)) {
    return 'Ozon ID должен содержать только цифры (без дефисов и букв).';
  }
  if (!data.point) return 'Выберите точку выдачи.';
  return null;
}

export function listActiveClients(db: Db): ClientRow[] {
  return db.all<ClientRow>('SELECT * FROM clients WHERE is_active = 1 ORDER BY ozon_client_id');
}

export function getClient(db: Db, id: number): ClientRow | undefined {
  return db.get<ClientRow>('SELECT * FROM clients WHERE id = ?', [id]);
}

/** Клиент с тем же нормализованным Ozon ID (среди всех, включая удалённых). */
function findByNorm(db: Db, norm: string, exceptId?: number): ClientRow | undefined {
  return db.all<ClientRow>('SELECT * FROM clients ORDER BY id')
    .find((c) => c.id !== exceptId && normalizeOzonId(c.ozon_client_id) === norm);
}

function duplicate(form: ClientForm): ClientResult {
  return { ok: false, error: `Клиент с ID ${form.ozonClientId} уже существует.` };
}

export function addClient(db: Db, form: ClientForm, clock: Clock = systemClock): ClientResult {
  const error = validateClient(form);
  if (error) return { ok: false, error };
  const data = clean(form);
  const norm = normalizeOzonId(data.ozonClientId);
  // Как на ПК: удалённый (неактивный) клиент тоже считается дубликатом.
  if (findByNorm(db, norm)) return duplicate(data);
  const created = clock.now().toSql();
  const updated = clock.now().toSql();
  const id = db.run(
    'INSERT INTO clients (ozon_client_id, full_name, phone, fixed_delivery_point, notes, ' +
      'is_active, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, 1, ?, ?)',
    [norm, data.fullName || null, data.phone || null, data.point, created, updated],
  ).lastInsertRowId;
  return { ok: true, id };
}

export function editClient(
  db: Db, id: number, form: ClientForm, clock: Clock = systemClock,
): ClientResult {
  const client = getClient(db, id);
  if (!client) return { ok: false, error: 'Клиент не найден.' };
  const error = validateClient(form);
  if (error) return { ok: false, error };
  const data = clean(form);
  const norm = normalizeOzonId(data.ozonClientId);
  if (norm !== normalizeOzonId(client.ozon_client_id) && findByNorm(db, norm, id)) {
    return duplicate(data);
  }
  const next = {
    ozon_client_id: norm,
    full_name: data.fullName || null,
    phone: data.phone || null,
    fixed_delivery_point: data.point,
  };
  const changed = (Object.keys(next) as Array<keyof typeof next>)
    .some((k) => next[k] !== client[k]);
  if (changed) {
    db.run(
      'UPDATE clients SET ozon_client_id = ?, full_name = ?, phone = ?, ' +
        'fixed_delivery_point = ?, updated_at = ? WHERE id = ?',
      [next.ozon_client_id, next.full_name, next.phone, next.fixed_delivery_point,
        clock.now().toSql(), id],
    );
  }
  return { ok: true, id };
}

/** «Удалить»: клиент становится неактивным, история посылок сохраняется. */
export function deactivateClient(db: Db, id: number, clock: Clock = systemClock): boolean {
  const client = getClient(db, id);
  if (!client) return false;
  if (client.is_active) {
    db.run('UPDATE clients SET is_active = 0, updated_at = ? WHERE id = ?',
      [clock.now().toSql(), id]);
  }
  return true;
}
