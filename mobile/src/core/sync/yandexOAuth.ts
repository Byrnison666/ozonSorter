/**
 * Вход в Яндекс по коду устройства (OAuth device flow) — перенос src/yandex_oauth.py.
 *
 * Приложение показывает короткий код, пользователь вводит его на ya.ru/device в
 * браузере, где вошёл в Яндекс, — пароль от Яндекса приложение не видит.
 * Токен даёт доступ только к папке приложения на Диске.
 *
 * Секрет приложения в публичный репозиторий не попадает: он приходит при
 * сборке из mobile/.env.local (EXPO_PUBLIC_YANDEX_CLIENT_SECRET, в .gitignore).
 */
import { formEncode, httpRequest, type Transport, YaDiskError } from './yadisk';

export const OAUTH_URL = 'https://oauth.yandex.ru';
export const INFO_URL = 'https://login.yandex.ru/info';
export const CLIENT_ID = '23f4d228c2c940d1a2d16a94764029a0';
export const SCOPE = 'cloud_api:disk.app_folder';
const TIMEOUT_MS = 20_000;
const MAX_BYTES = 64 * 1024;

export class OAuthError extends Error {
  /** code — код ошибки Яндекса (expired_token, access_denied…). */
  constructor(message: string, readonly code = '') {
    super(message);
  }
}

export interface DeviceCode {
  deviceCode: string;
  userCode: string;
  verificationUrl: string;
  /** Секунд между опросами. */
  interval: number;
  /** Date.now(), после которого код недействителен. */
  expiresAt: number;
}

export interface Tokens {
  accessToken: string;
  refreshToken: string;
  /** Unix-время (секунды) окончания действия accessToken. */
  expiresAt: number;
}

export interface OAuthEnv {
  transport: Transport;
  /** Пустой — в сборке нет секрета приложения. */
  clientSecret: string;
  oauthUrl?: string;
  infoUrl?: string;
}

function clientSecret(env: OAuthEnv): string {
  if (!env.clientSecret) throw new OAuthError('This build has no Yandex app secret', 'not_configured');
  return env.clientSecret;
}

async function post(env: OAuthEnv, endpoint: string, fields: Record<string, string>) {
  return httpRequest(env.transport, {
    method: 'POST', url: `${env.oauthUrl ?? OAUTH_URL}${endpoint}`,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
    body: new TextEncoder().encode(formEncode(fields)), timeoutMs: TIMEOUT_MS, maxBytes: MAX_BYTES,
  });
}

const errorCode = (data: Record<string, unknown>) => (typeof data.error === 'string' ? data.error : '');

function tokens(data: Record<string, unknown>): Tokens {
  const { access_token: access, refresh_token: refresh = '', expires_in: expiresIn } = data;
  if (typeof access !== 'string' || !access || typeof refresh !== 'string'
      || typeof expiresIn !== 'number' || !Number.isInteger(expiresIn)) {
    throw new OAuthError('Malformed token response', 'bad_response');
  }
  return { accessToken: access, refreshToken: refresh, expiresAt: Math.floor(Date.now() / 1000) + expiresIn };
}

export async function requestDeviceCode(env: OAuthEnv, deviceName: string): Promise<DeviceCode> {
  const response = await post(env, '/device/code', {
    client_id: CLIENT_ID, device_name: deviceName.slice(0, 100) || 'OzonSorter', scope: SCOPE,
  });
  const data = response.json();
  if (response.status !== 200) {
    throw new OAuthError(`Device code request failed with HTTP ${response.status}`, errorCode(data));
  }
  const { device_code: deviceCode, user_code: userCode, verification_url: url } = data;
  const { interval = 5, expires_in: expiresIn } = data;
  if (typeof deviceCode !== 'string' || !deviceCode || typeof userCode !== 'string' || !userCode
      || typeof url !== 'string' || !url.startsWith('https://')
      || typeof interval !== 'number' || !Number.isInteger(interval)
      || typeof expiresIn !== 'number' || !Number.isInteger(expiresIn)) {
    throw new OAuthError('Malformed device code response', 'bad_response');
  }
  return {
    deviceCode, userCode, verificationUrl: url,
    interval: Math.max(1, Math.min(interval, 30)), expiresAt: Date.now() + expiresIn * 1000,
  };
}

/** Токены, если пользователь уже подтвердил код; null — ещё ждём. */
export async function pollToken(env: OAuthEnv, code: DeviceCode): Promise<Tokens | null> {
  if (Date.now() > code.expiresAt) throw new OAuthError('Device code expired', 'expired_token');
  const response = await post(env, '/token', {
    grant_type: 'device_code', code: code.deviceCode, client_id: CLIENT_ID, client_secret: clientSecret(env),
  });
  const data = response.json();
  if (response.status === 200) return tokens(data);
  const error = errorCode(data);
  if (error === 'authorization_pending' || error === 'slow_down') return null;
  throw new OAuthError(`Token request failed with HTTP ${response.status}`, error);
}

export async function refreshTokens(env: OAuthEnv, refreshToken: string): Promise<Tokens> {
  const response = await post(env, '/token', {
    grant_type: 'refresh_token', refresh_token: refreshToken, client_id: CLIENT_ID, client_secret: clientSecret(env),
  });
  const data = response.json();
  if (response.status !== 200) {
    throw new OAuthError(`Token refresh failed with HTTP ${response.status}`, errorCode(data));
  }
  const result = tokens(data);
  // Яндекс может не прислать новый refresh_token — тогда действует прежний.
  return result.refreshToken ? result : { ...result, refreshToken };
}

/** uid и логин владельца токена. */
export async function fetchUser(env: OAuthEnv, accessToken: string): Promise<{ uid: string; login: string }> {
  const response = await httpRequest(env.transport, {
    method: 'GET', url: `${env.infoUrl ?? INFO_URL}?format=json`,
    headers: { Authorization: `OAuth ${accessToken}` }, timeoutMs: TIMEOUT_MS, maxBytes: MAX_BYTES,
  });
  if (response.status !== 200) {
    throw new YaDiskError(`User info request failed with HTTP ${response.status}`, response.status);
  }
  const { id: uid, login = '' } = response.json();
  if (typeof uid !== 'string' || !/^\d+$/.test(uid) || typeof login !== 'string') {
    throw new OAuthError('Malformed user info response', 'bad_response');
  }
  return { uid, login };
}
