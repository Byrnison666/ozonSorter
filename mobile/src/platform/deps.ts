/** Зависимости ядра на телефоне: файлы, sha256, SQLite, часы, настройки. */
import { CryptoDigestAlgorithm, digest } from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

import { systemClock } from '../core/models';
import {
  adoptLegacyState, defaultConfig, parseConfigFile, serializeConfig, type SyncConfig,
} from '../core/sync/config';
import type { SyncDeps } from '../core/sync/syncService';
import { DATA_DIR, openExpoDb } from './expoDb';
import { expoFiles } from './files';

export const DB_NAME = 'ozon_sorter.db';
export const DB_PATH = `${DATA_DIR}/${DB_NAME}`;
export const STATE_PATH = `${DATA_DIR}/sync_state.json`;
const CONFIG_PATH = `${DATA_DIR}/sync.json`;
const TOKEN_KEY = 'yandex_token';
const REFRESH_KEY = 'yandex_refresh_token';
// Пароль приложения WebDAV-версии: после перехода на вход через Яндекс не нужен.
const LEGACY_PASSWORD_KEY = 'webdav_password';

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

/** Настройки: токены — в Android Keystore (SecureStore), остальное — в файле. */
export async function loadConfig(): Promise<SyncConfig> {
  try {
    const text = await expoFiles.exists(CONFIG_PATH) ? await expoFiles.readText(CONFIG_PATH) : '{}';
    const config = await parseConfigFile(text, sha256);
    if (config.uid) {
      config.token = (await SecureStore.getItemAsync(TOKEN_KEY)) ?? '';
      config.refreshToken = config.token ? (await SecureStore.getItemAsync(REFRESH_KEY)) ?? '' : '';
    }
    return config;
  } catch (e) {
    console.warn('Sync config is unreadable, using defaults', e);
    return defaultConfig();
  }
}

async function writeFileAtomic(path: string, text: string): Promise<void> {
  await expoFiles.mkdirs(DATA_DIR);
  const tmp = `${path}.tmp`;
  await expoFiles.write(tmp, text);
  await expoFiles.replace(tmp, path);
}

async function storeSecret(key: string, value: string): Promise<void> {
  if (value) await SecureStore.setItemAsync(key, value);
  else await SecureStore.deleteItemAsync(key);
}

export async function saveConfig(config: SyncConfig): Promise<void> {
  // Сначала токены: файл с uid без токена — «не настроено», а не битые настройки.
  await storeSecret(TOKEN_KEY, config.token);
  await storeSecret(REFRESH_KEY, config.token ? config.refreshToken : '');
  await writeFileAtomic(CONFIG_PATH, serializeConfig(config));
  if (!config.legacyTargetId) await SecureStore.deleteItemAsync(LEGACY_PASSWORD_KEY);
}

/** После первого входа: перенести состояние синхронизации WebDAV-версии (см. adoptLegacyState). */
export async function adoptLegacySyncState(config: SyncConfig): Promise<boolean> {
  try {
    if (!(await expoFiles.exists(STATE_PATH))) return false;
    const adopted = await adoptLegacyState(config, await expoFiles.readText(STATE_PATH), sha256);
    if (adopted === null) return false;
    await writeFileAtomic(STATE_PATH, adopted);
    return true;
  } catch (e) {
    console.warn('Legacy sync state was not adopted', e);
    return false;
  }
}
