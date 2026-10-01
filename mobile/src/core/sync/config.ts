/** Настройки синхронизации — как SyncConfig ПК (src/sync_config.py). */

export const DEFAULT_URL = 'https://webdav.yandex.ru';
export const DEFAULT_REMOTE_DIR = 'OzonSorter';
export const DEFAULT_DEVICE_NAME = 'Телефон';

export interface SyncConfig {
  url: string;
  login: string;
  password: string;
  remoteDir: string;
  deviceName: string;
}

export function defaultConfig(): SyncConfig {
  return {
    url: DEFAULT_URL, login: '', password: '', remoteDir: DEFAULT_REMOTE_DIR,
    deviceName: DEFAULT_DEVICE_NAME,
  };
}

export function isConfigured(c: SyncConfig): boolean {
  return Boolean(c.url && c.login && c.password);
}

/** Куда синхронизируемся (сервер, аккаунт, папка): после смены старое состояние неприменимо. */
export async function targetId(c: SyncConfig, sha256: (data: Uint8Array) => Promise<string>): Promise<string> {
  const key = `${c.url}|${c.login}|${c.remoteDir.replace(/^\/+|\/+$/g, '')}`;
  return (await sha256(new TextEncoder().encode(key))).slice(0, 16);
}
