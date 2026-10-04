/** Настройки синхронизации — как SyncConfig ПК (src/sync_config.py). */
import { API_URL } from './yadisk';

/** Папка синхронизации внутри app:/ (корень — «Приложения/OzonSorter»). */
export const REMOTE_DIR = '';
export const DEFAULT_DEVICE_NAME = 'Телефон';
// Версии до 1.1.0 работали по WebDAV с папкой OzonSorter в корне Диска.
export const LEGACY_URL = 'https://webdav.yandex.ru';
export const LEGACY_REMOTE_DIR = 'OzonSorter';
// Токен действует год; обновляем его за месяц до конца.
const REFRESH_BEFORE_S = 30 * 24 * 3600;

export interface SyncConfig {
  token: string;
  refreshToken: string;
  /** Unix-время (секунды); 0 — неизвестно. */
  tokenExpiresAt: number;
  /** Аккаунт Яндекса, к которому выдан токен. */
  uid: string;
  /** Для показа в настройках. */
  login: string;
  deviceName: string;
  /** Не сохраняются: адрес API и папку меняют только тесты. */
  apiUrl: string;
  remoteDir: string;
  /**
   * Настройки WebDAV-версии: по ним при первом входе через Яндекс узнаётся
   * старое состояние синхронизации (adoptLegacyState).
   */
  legacyLogin: string;
  legacyTargetId: string;
}

export function defaultConfig(): SyncConfig {
  return {
    token: '', refreshToken: '', tokenExpiresAt: 0, uid: '', login: '',
    deviceName: DEFAULT_DEVICE_NAME, apiUrl: API_URL, remoteDir: REMOTE_DIR,
    legacyLogin: '', legacyTargetId: '',
  };
}

export function isConfigured(c: SyncConfig): boolean {
  return Boolean(c.token && c.uid);
}

/** До конца действия токена меньше месяца — пора обновить. */
export function needsRefresh(c: SyncConfig, nowS = Date.now() / 1000): boolean {
  return Boolean(c.refreshToken && c.tokenExpiresAt && c.tokenExpiresAt - nowS <= REFRESH_BEFORE_S);
}

type Sha256 = (data: Uint8Array) => Promise<string>;

async function shortHash(key: string, sha256: Sha256): Promise<string> {
  return (await sha256(new TextEncoder().encode(key))).slice(0, 16);
}

const trimSlashes = (s: string) => s.replace(/^\/+|\/+$/g, '');

/** Куда синхронизируемся (API, аккаунт, папка): после смены старое состояние неприменимо. */
export function targetId(c: SyncConfig, sha256: Sha256): Promise<string> {
  return shortHash(`${c.apiUrl}|${c.uid}|${trimSlashes(c.remoteDir)}`, sha256);
}

/** targetId из настроек WebDAV-версии — тем же способом, что она считала. */
export function legacyTargetId(url: string, login: string, remoteDir: string, sha256: Sha256): Promise<string> {
  return shortHash(`${url}|${login}|${trimSlashes(remoteDir)}`, sha256);
}

function normalizeLogin(login: string): string {
  let result = login.trim().toLowerCase();
  for (const domain of ['@yandex.ru', '@ya.ru']) {
    if (result.endsWith(domain)) result = result.slice(0, -domain.length);
  }
  // Яндекс считает точку и дефис в логине одним и тем же символом.
  return result.replaceAll('.', '-');
}

/**
 * Состояние синхронизации после первого входа через Яндекс: если раньше это
 * устройство синхронизировалось по WebDAV тем же аккаунтом, состояние
 * переносится на новое место (базу на Диске переносят в папку приложения
 * заранее). Иначе устройство считалось бы ни разу не синхронизированным.
 *
 * Другой аккаунт — не переносится: чужая база не должна выглядеть
 * продолжением этой. Возвращает новое содержимое файла состояния или null.
 */
export async function adoptLegacyState(c: SyncConfig, stateText: string, sha256: Sha256): Promise<string | null> {
  if (!c.legacyTargetId || !c.legacyLogin || !c.login) return null;
  if (normalizeLogin(c.legacyLogin) !== normalizeLogin(c.login)) return null;
  let state: unknown;
  try {
    state = JSON.parse(stateText);
  } catch {
    return null;
  }
  if (!state || typeof state !== 'object' || Array.isArray(state)) return null;
  const record = state as Record<string, unknown>;
  if (record.target !== c.legacyTargetId) return null;
  return JSON.stringify({ ...record, target: await targetId(c, sha256) });
}

/** Настройки WebDAV-версии больше не нужны: вход через Яндекс выполнен. */
export function forgetLegacy(c: SyncConfig): SyncConfig {
  return { ...c, legacyLogin: '', legacyTargetId: '' };
}

/**
 * Настройки из sync.json (токены в нём не хранятся — они в SecureStore).
 * Файл WebDAV-версии ({url, login, remoteDir}) превращается в legacy-поля.
 */
export async function parseConfigFile(text: string, sha256: Sha256): Promise<SyncConfig> {
  const config = defaultConfig();
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return config;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return config;
  const d = data as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  config.deviceName = str(d.deviceName) || config.deviceName;
  config.uid = str(d.uid);
  config.login = str(d.login);
  config.tokenExpiresAt = typeof d.tokenExpiresAt === 'number' && Number.isInteger(d.tokenExpiresAt)
    ? d.tokenExpiresAt : 0;
  config.legacyLogin = str(d.legacyLogin);
  config.legacyTargetId = str(d.legacyTargetId);
  if ('url' in d) {
    // Файл WebDAV-версии: запоминаем, откуда она синхронизировалась.
    config.login = '';
    config.uid = '';
    const login = str(d.login);
    if (login) {
      config.legacyLogin = login;
      config.legacyTargetId = await legacyTargetId(
        str(d.url) || LEGACY_URL, login, str(d.remoteDir) || LEGACY_REMOTE_DIR, sha256,
      );
    }
  }
  return config;
}

/** Содержимое sync.json — без токенов, адреса API и папки. */
export function serializeConfig(c: SyncConfig): string {
  const data: Record<string, unknown> = {
    deviceName: c.deviceName, uid: c.uid, login: c.login, tokenExpiresAt: c.tokenExpiresAt,
  };
  if (c.legacyTargetId) {
    data.legacyLogin = c.legacyLogin;
    data.legacyTargetId = c.legacyTargetId;
  }
  return JSON.stringify(data);
}
