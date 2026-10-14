/**
 * «Настройки» — Яндекс.Диск (settings_screen.py ПК): вход по коду, имя устройства, ручная синхронизация;
 * переход к резервным копиям базы на телефоне.
 */
import { type NavigationProp, type ParamListBase, useNavigation } from '@react-navigation/native';
import { useEffect, useRef, useState } from 'react';
import { Linking, StyleSheet } from 'react-native';

import { useApp } from '../app/AppContext';
import { forgetLegacy, isConfigured, type SyncConfig } from '../core/sync/config';
import { describeLoginError } from '../core/sync/flow';
import { type DeviceCode, fetchUser, pollToken, requestDeviceCode } from '../core/sync/yandexOAuth';
import { adoptLegacySyncState } from '../platform/deps';
import { oauthEnv } from '../platform/yandex';
import {
  Badge, Button, Card, Field, Screen, styles as ui, Text,
} from '../ui/components';
import { colors, radius } from '../ui/theme';

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const hhmm = (d: Date) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;

export function SettingsScreen() {
  const app = useApp();
  const navigation = useNavigation<NavigationProp<ParamListBase>>();
  const [deviceName, setDeviceName] = useState('');
  const [status, setStatus] = useState('');
  // Идёт вход: code null — код ещё запрашивается.
  const [login, setLogin] = useState<{ code: DeviceCode | null } | null>(null);
  // Номер текущей попытки входа: отмена и уход с экрана его меняют, и
  // запоздавшие ответы прежней попытки отбрасываются.
  const loginRun = useRef(0);

  useEffect(() => {
    setDeviceName(app.config.deviceName);
  }, [app.config]);

  useEffect(() => () => {
    loginRun.current += 1;
  }, []);

  function collect(): SyncConfig {
    return { ...app.config, deviceName: deviceName.trim() || app.config.deviceName };
  }

  async function save(c: SyncConfig): Promise<boolean> {
    try {
      await app.saveSettings(c);
      return true;
    } catch (e) {
      await app.message('Ошибка', `Не удалось сохранить настройки:\n${(e as Error).message}`);
      return false;
    }
  }

  async function onSave() {
    if (await save(collect())) setStatus('Настройки сохранены.');
  }

  async function onLogin() {
    const run = ++loginRun.current;
    const current = () => loginRun.current === run;
    setLogin({ code: null });
    setStatus('');
    try {
      const code = await requestDeviceCode(oauthEnv, collect().deviceName);
      if (!current()) return;
      setLogin({ code });
      for (;;) {
        await sleep(code.interval * 1000);
        if (!current()) return;
        const tokens = await pollToken(oauthEnv, code);
        if (!current()) return;
        if (!tokens) continue;
        const user = await fetchUser(oauthEnv, tokens.accessToken);
        if (!current()) return;
        const config: SyncConfig = {
          ...collect(), token: tokens.accessToken, refreshToken: tokens.refreshToken,
          tokenExpiresAt: tokens.expiresAt, uid: user.uid, login: user.login,
        };
        await adoptLegacySyncState(config);
        setLogin(null);
        if (await save(forgetLegacy(config))) setStatus(`Вход выполнен в ${hhmm(new Date())}.`);
        return;
      }
    } catch (e) {
      if (!current()) return;
      setLogin(null);
      await app.message('Вход в Яндекс', describeLoginError(e));
    }
  }

  function onCancelLogin() {
    loginRun.current += 1;
    setLogin(null);
  }

  async function onLogout() {
    const yes = await app.ask('Выход из Яндекса', 'Выйти из Яндекса? Синхронизация остановится до нового входа.', [
      { text: 'Отмена', value: false, style: 'cancel' }, { text: 'Выйти', value: true, style: 'destructive' },
    ], false);
    if (!yes) return;
    const config = { ...collect(), token: '', refreshToken: '', tokenExpiresAt: 0, uid: '', login: '' };
    if (await save(config)) setStatus('Выход выполнен.');
  }

  async function onSync() {
    const config = collect();
    if (!isConfigured(config)) {
      await app.message('Нужен вход', 'Сначала войдите в Яндекс.');
      return;
    }
    if (!(await save(config))) return;
    const ok = await app.syncNow({ announce: true });
    setStatus(ok ? `Синхронизировано в ${hhmm(new Date())}.` : 'Синхронизация не выполнена.');
  }

  const code = login?.code;
  return (
    <Screen>
      <Card title="Яндекс.Диск" subtitle="База синхронизируется с компьютером через папку на Яндекс.Диске.">
        {login ? (
          <>
            <Text style={ui.muted}>
              {code
                ? `Откройте страницу ${code.verificationUrl.replace('https://', '')} в браузере, где вы вошли в Яндекс, и введите код:`
                : 'Получение кода…'}
            </Text>
            {code ? <Text selectable style={styles.code}>{code.userCode}</Text> : null}
            {code ? (
              <Button
                kind="primary" icon="external-link" title="Открыть страницу"
                onPress={() => void Linking.openURL(code.verificationUrl)}
              />
            ) : null}
            <Button title="Отмена" onPress={onCancelLogin} />
          </>
        ) : (
          <>
            <Text style={ui.muted}>
              {app.configured
                ? `Вход выполнен: ${app.config.login || 'аккаунт Яндекса'}. База хранится на Диске в папке «Приложения/OzonSorter».`
                : 'Вход в Яндекс не выполнен — синхронизация выключена.'}
            </Text>
            {app.configured
              ? <Button icon="log-out" title="Выйти из Яндекса" onPress={onLogout} />
              : <Button kind="primary" icon="log-in" title="Войти в Яндекс" onPress={onLogin} />}
          </>
        )}
        <Field label="Имя устройства" placeholder="как подписывать это устройство, например «Телефон Ани»"
          value={deviceName} onChangeText={setDeviceName} />
        <Text style={ui.mutedSmall}>
          Пароль от Яндекса приложение не видит: вход подтверждается на странице Яндекса. Доступ есть только
          к папке приложения на Диске; токен хранится в защищённом хранилище Android.
        </Text>
        <Button icon="save" title="Сохранить" onPress={onSave} />
        <Button icon="refresh-cw" title="Синхронизировать" onPress={onSync} busy={app.syncBusy} disabled={!app.configured} />
        {status ? <Text style={ui.muted}>{status}</Text> : null}
      </Card>
      {app.configured ? (
        <Badge
          tone={app.dirty === false && !app.diskUnavailable ? 'success' : 'warning'}
          dot
          text={app.diskUnavailable
            ? 'Яндекс.Диск недоступен — работайте как обычно, изменения выложатся, когда он заработает'
            : app.dirty === null ? 'Яндекс.Диск: не удалось проверить изменения'
              : app.dirty ? 'Яндекс.Диск: есть невыложенные изменения' : 'Яндекс.Диск: невыложенных изменений нет'}
        />
      ) : null}
      <Text style={ui.mutedSmall}>
        Работайте по очереди: перед работой на телефоне база загружается с Диска, после правок — выкладывается.
        Если изменить базу и на компьютере, и на телефоне, приложение предложит выбрать одну версию.
      </Text>
      <Card title="Резервные копии" subtitle="Копии базы на этом телефоне — на случай, если Яндекс.Диск недоступен.">
        <Button icon="archive" title="Резервные копии" onPress={() => navigation.navigate('backups')} />
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  code: {
    fontSize: 28, lineHeight: 36, fontWeight: '600', letterSpacing: 4, textAlign: 'center', color: colors.text,
    backgroundColor: colors.surfaceAlt, borderRadius: radius.md, paddingVertical: 10, overflow: 'hidden',
  },
});
