/** Зависимости ядра на телефоне: файлы, sha256, SQLite, часы, настройки. */
import { CryptoDigestAlgorithm, digest } from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

import { systemClock } from '../core/models';
import { defaultConfig, type SyncConfig } from '../core/sync/config';
import type { SyncDeps } from '../core/sync/syncService';
import { DATA_DIR, openExpoDb } from './expoDb';
import { expoFiles } from './files';

export const DB_NAME = 'ozon_sorter.db';
export const DB_PATH = `${DATA_DIR}/${DB_NAME}`;
export const STATE_PATH = `${DATA_DIR}/sync_state.json`;
const CONFIG_PATH = `${DATA_DIR}/sync.json`;
const PASSWORD_KEY = 'webdav_password';

export async function sha256(data: Uint8Array): Promise<string> {
  const hash = new Uint8Array(await digest(CryptoDigestAlgorithm.SHA256, data as Uint8Array<ArrayBuffer>));
  return Array.from(hash, (b) => b.toString(16).padStart(2, '0')).join('');
}

export const deviceDeps: SyncDeps = {
  files: expoFiles,
  sha256,
  openDb: openExpoDb,
  clock: systemClock,
};

/** Настройки: пароль — в Android Keystore (SecureStore), остальное — в файле. */
export async function loadConfig(): Promise<SyncConfig> {
  const config = defaultConfig();
  try {
    if (await expoFiles.exists(CONFIG_PATH)) {
      const data = JSON.parse(await expoFiles.readText(CONFIG_PATH));
      if (data && typeof data === 'object') {
        config.url = String(data.url || config.url);
        config.login = String(data.login || '');
        config.remoteDir = String(data.remoteDir || config.remoteDir);
        config.deviceName = String(data.deviceName || config.deviceName);
      }
    }
    config.password = (await SecureStore.getItemAsync(PASSWORD_KEY)) ?? '';
  } catch (e) {
    console.warn('Sync config is unreadable, using defaults', e);
  }
  return config;
}

export async function saveConfig(config: SyncConfig): Promise<void> {
  await expoFiles.mkdirs(DATA_DIR);
  const tmp = `${CONFIG_PATH}.tmp`;
  const { url, login, remoteDir, deviceName } = config;
  await expoFiles.write(tmp, JSON.stringify({ url, login, remoteDir, deviceName }));
  await expoFiles.replace(tmp, CONFIG_PATH);
  if (config.password) await SecureStore.setItemAsync(PASSWORD_KEY, config.password);
  else await SecureStore.deleteItemAsync(PASSWORD_KEY);
}
