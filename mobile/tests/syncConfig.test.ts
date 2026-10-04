/**
 * Настройки синхронизации (переход с WebDAV-версии, обновление токена) и
 * показ хода передачи — как tests/test_sync_config.py и format_size ПК.
 */
import { describe, expect, test } from 'bun:test';

import {
  adoptLegacyState, defaultConfig, forgetLegacy, isConfigured, legacyTargetId, LEGACY_URL, needsRefresh,
  parseConfigFile, serializeConfig, type SyncConfig, targetId,
} from '../src/core/sync/config';
import { formatSize, SpeedMeter, transferText } from '../src/core/sync/flow';
import { sha256Hex } from './support/syncEnv';

const sha256 = async (data: Uint8Array) => sha256Hex(data);

const logged = (over: Partial<SyncConfig> = {}): SyncConfig => ({
  ...defaultConfig(), token: 't', uid: '1001', login: 'ivan.petrov', ...over,
});

describe('настройки', () => {
  test('вход выполнен — нужен и токен, и аккаунт', () => {
    expect(isConfigured(defaultConfig())).toBe(false);
    expect(isConfigured(logged())).toBe(true);
    expect(isConfigured(logged({ uid: '' }))).toBe(false);
  });

  test('токен обновляется за месяц до конца действия', () => {
    const now = 1_800_000_000;
    const day = 24 * 3600;
    expect(needsRefresh(logged({ refreshToken: 'r', tokenExpiresAt: now + 31 * day }), now)).toBe(false);
    expect(needsRefresh(logged({ refreshToken: 'r', tokenExpiresAt: now + 29 * day }), now)).toBe(true);
    expect(needsRefresh(logged({ refreshToken: '', tokenExpiresAt: now }), now)).toBe(false);
    expect(needsRefresh(logged({ refreshToken: 'r', tokenExpiresAt: 0 }), now)).toBe(false);
  });

  test('sync.json без токенов, адреса API и папки; читается обратно', async () => {
    const c = logged({ refreshToken: 'r', tokenExpiresAt: 123, deviceName: 'Телефон Ани', remoteDir: 'test' });
    const text = serializeConfig(c);
    expect(text).not.toContain('"t"');
    expect(JSON.parse(text)).toEqual({ deviceName: 'Телефон Ани', uid: '1001', login: 'ivan.petrov', tokenExpiresAt: 123 });
    const back = await parseConfigFile(text, sha256);
    expect(back).toMatchObject({ deviceName: 'Телефон Ани', uid: '1001', login: 'ivan.petrov', tokenExpiresAt: 123 });
    expect(back.token).toBe('');
    expect(back.remoteDir).toBe('');
  });

  test('повреждённый sync.json — настройки по умолчанию', async () => {
    for (const text of ['{broken', '[]', 'null', '5']) {
      expect(await parseConfigFile(text, sha256)).toEqual(defaultConfig());
    }
  });

  test('targetId зависит от аккаунта и папки', async () => {
    const a = await targetId(logged(), sha256);
    expect(a).toMatch(/^[0-9a-f]{16}$/);
    expect(await targetId(logged({ uid: '1002' }), sha256)).not.toBe(a);
    expect(await targetId(logged({ remoteDir: 'x' }), sha256)).not.toBe(a);
    expect(await targetId(logged({ remoteDir: '/x/' }), sha256)).toBe(await targetId(logged({ remoteDir: 'x' }), sha256));
  });
});

describe('переход с WebDAV-версии', () => {
  const legacyFile = JSON.stringify({ url: LEGACY_URL, login: 'Ivan-Petrov@yandex.ru', remoteDir: 'OzonSorter', deviceName: 'Тел' });

  test('файл WebDAV-версии превращается в legacy-поля, вход не считается выполненным', async () => {
    const c = await parseConfigFile(legacyFile, sha256);
    expect(c.deviceName).toBe('Тел');
    expect(c.login).toBe('');
    expect(isConfigured({ ...c, token: 'x' })).toBe(false);
    expect(c.legacyLogin).toBe('Ivan-Petrov@yandex.ru');
    expect(c.legacyTargetId).toBe(await legacyTargetId(LEGACY_URL, 'Ivan-Petrov@yandex.ru', 'OzonSorter', sha256));
    // До входа legacy-поля сохраняются, после — забываются.
    expect(JSON.parse(serializeConfig(c)).legacyTargetId).toBe(c.legacyTargetId);
    expect(JSON.parse(serializeConfig(forgetLegacy(c))).legacyTargetId).toBeUndefined();
  });

  test('тот же аккаунт — состояние переносится на новое место', async () => {
    const legacy = await parseConfigFile(legacyFile, sha256);
    const c = { ...legacy, token: 't', uid: '1001', login: 'ivan.petrov' }; // точка и дефис — одно и то же
    const state = JSON.stringify({ target: legacy.legacyTargetId, base_rev: 5, base_sha256: 'ab', synced_at: 'x' });
    const adopted = await adoptLegacyState(c, state, sha256);
    expect(JSON.parse(adopted!)).toEqual({ target: await targetId(c, sha256), base_rev: 5, base_sha256: 'ab', synced_at: 'x' });
  });

  test('другой аккаунт, чужое состояние или мусор — не переносится', async () => {
    const legacy = await parseConfigFile(legacyFile, sha256);
    const state = JSON.stringify({ target: legacy.legacyTargetId, base_rev: 5 });
    const c = { ...legacy, token: 't', uid: '1001', login: 'ivan.petrov' };
    expect(await adoptLegacyState({ ...c, login: 'someone' }, state, sha256)).toBeNull();
    expect(await adoptLegacyState(c, JSON.stringify({ target: 'other', base_rev: 5 }), sha256)).toBeNull();
    expect(await adoptLegacyState(c, '{broken', sha256)).toBeNull();
    expect(await adoptLegacyState(c, '[1]', sha256)).toBeNull();
    expect(await adoptLegacyState(forgetLegacy(c), state, sha256)).toBeNull();
  });
});

describe('ход передачи', () => {
  test('размер — как на ПК', () => {
    expect(formatSize(0)).toBe('0 КБ');
    expect(formatSize(850 * 1024)).toBe('850 КБ');
    expect(formatSize(1024 * 1024)).toBe('1,0 МБ');
    expect(formatSize(3.44 * 1024 * 1024)).toBe('3,4 МБ');
  });

  test('текст: сколько из скольки и скорость', () => {
    expect(transferText(1024 * 1024, 3.5 * 1024 * 1024, 450 * 1024)).toBe('1,0 МБ из 3,5 МБ · 450 КБ/с');
    expect(transferText(200 * 1024, 0, 0)).toBe('200 КБ');
  });

  test('скорость — за последние 3 секунды', () => {
    const m = new SpeedMeter();
    expect(m.hasData).toBe(false);
    m.add(0, 0);
    expect(m.speed).toBe(0);
    m.add(100_000, 1000);
    expect(m.speed).toBe(100_000);
    // Через 10 с быстрый старт забыт: в окне только медленный хвост.
    m.add(110_000, 9000);
    m.add(120_000, 10_000);
    expect(m.speed).toBe(10_000);
  });
});
