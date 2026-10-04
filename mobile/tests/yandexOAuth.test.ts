/**
 * Вход в Яндекс по коду устройства — против тестового сервера ПК
 * (tests/yadisk_fake.py), как tests/test_yandex_oauth.py.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';

import { YaDiskError } from '../src/core/sync/yadisk';
import {
  fetchUser, OAuthError, type OAuthEnv, pollToken, refreshTokens, requestDeviceCode,
} from '../src/core/sync/yandexOAuth';
import { type DavServer, startDavServer, testTransport } from './support/syncEnv';

let server: DavServer;
let env: OAuthEnv;

beforeAll(async () => {
  server = await startDavServer();
  env = { transport: testTransport, clientSecret: 'test-secret', oauthUrl: server.oauthUrl, infoUrl: server.infoUrl };
});
afterAll(() => server.stop());
beforeEach(() => server.control({ cmd: 'clear' }));

describe('вход по коду устройства', () => {
  test('код, ожидание подтверждения, токены и владелец', async () => {
    const code = await requestDeviceCode(env, 'Телефон');
    expect(code.userCode).toBe('TESTCODE');
    expect(code.verificationUrl).toBe('https://ya.ru/device');
    expect(code.expiresAt).toBeGreaterThan(Date.now());
    expect(await pollToken(env, code)).toBeNull();
    await server.control({ cmd: 'set', approved: true });
    const tokens = await pollToken(env, code);
    expect(tokens).toMatchObject({ accessToken: 'test-token', refreshToken: 'refresh-1' });
    const yearS = 365 * 24 * 3600;
    expect(Math.abs(tokens!.expiresAt - (Date.now() / 1000 + yearS))).toBeLessThan(60);
    expect(await fetchUser(env, tokens!.accessToken)).toEqual({ uid: '1001', login: 'tester' });
  });

  test('устаревший код не опрашивается', async () => {
    const code = { ...(await requestDeviceCode(env, 'x')), expiresAt: Date.now() - 1 };
    const err = await pollToken(env, code).catch((e) => e);
    expect(err).toBeInstanceOf(OAuthError);
    expect(err.code).toBe('expired_token');
  });

  test('без секрета приложения — понятная ошибка, запрос не уходит', async () => {
    const code = await requestDeviceCode(env, 'x');
    const err = await pollToken({ ...env, clientSecret: '' }, code).catch((e) => e);
    expect(err.code).toBe('not_configured');
  });

  test('обновление токена; неверный refresh — ошибка Яндекса', async () => {
    expect(await refreshTokens(env, 'refresh-1')).toMatchObject({ accessToken: 'test-token', refreshToken: 'refresh-1' });
    const err = await refreshTokens(env, 'wrong').catch((e) => e);
    expect(err).toBeInstanceOf(OAuthError);
    expect(err.code).toBe('invalid_grant');
  });

  test('чужой токен — владельца не узнать', async () => {
    const err = await fetchUser(env, 'other').catch((e) => e);
    expect(err).toBeInstanceOf(YaDiskError);
    expect(err.isAuthError).toBe(true);
  });

  test('незащищённая страница ввода кода не принимается', async () => {
    const transport = {
      request: async () => ({
        status: 200, finalUrl: 'https://oauth.yandex.ru/device/code',
        body: new TextEncoder().encode(JSON.stringify({
          device_code: 'd', user_code: 'U', verification_url: 'http://ya.ru/device', interval: 5, expires_in: 300,
        })),
      }),
    };
    const err = await requestDeviceCode({ transport, clientSecret: 's' }, 'x').catch((e) => e);
    expect(err).toBeInstanceOf(OAuthError);
    expect(err.code).toBe('bad_response');
  });
});
