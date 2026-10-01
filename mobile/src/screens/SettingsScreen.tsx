/** «Настройки» — Яндекс.Диск (settings_screen.py ПК). Адрес сервера в форме не правится. */
import { useEffect, useState } from 'react';
import { Text } from 'react-native';

import { useApp } from '../app/AppContext';
import { DEFAULT_REMOTE_DIR, isConfigured, type SyncConfig } from '../core/sync/config';
import { Badge, Button, Card, Field, Screen, styles as ui } from '../ui/components';

export function SettingsScreen() {
  const app = useApp();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [remoteDir, setRemoteDir] = useState('');
  const [deviceName, setDeviceName] = useState('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    setLogin(app.config.login);
    setPassword(app.config.password);
    setRemoteDir(app.config.remoteDir);
    setDeviceName(app.config.deviceName);
  }, [app.config]);

  function collect(): SyncConfig {
    return {
      ...app.config,
      login: login.trim(),
      password: password.trim(),
      remoteDir: remoteDir.trim().replace(/^\/+|\/+$/g, '') || DEFAULT_REMOTE_DIR,
      deviceName: deviceName.trim() || app.config.deviceName,
    };
  }

  async function onSave(): Promise<boolean> {
    try {
      await app.saveSettings(collect());
    } catch (e) {
      await app.message('Ошибка', `Не удалось сохранить настройки:\n${(e as Error).message}`);
      return false;
    }
    setStatus('Настройки сохранены.');
    return true;
  }

  async function onSync() {
    if (!isConfigured(collect())) {
      await app.message('Нужны логин и пароль', 'Укажите логин Яндекса и пароль приложения.');
      return;
    }
    if (!(await onSave())) return;
    const ok = await app.syncNow({ announce: true });
    const now = new Date();
    setStatus(ok
      ? `Синхронизировано в ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}.`
      : 'Синхронизация не выполнена.');
  }

  return (
    <Screen>
      <Card title="Яндекс.Диск" subtitle="База синхронизируется с компьютером через папку на Яндекс.Диске.">
        <Field label="Логин" placeholder="логин Яндекса, например ivanov" autoCapitalize="none"
          autoCorrect={false} value={login} onChangeText={setLogin} />
        <Field label="Пароль приложения" placeholder="пароль приложения, не пароль от почты" secureTextEntry
          autoCapitalize="none" autoCorrect={false} value={password} onChangeText={setPassword} />
        <Field label="Папка на Диске" placeholder={DEFAULT_REMOTE_DIR} autoCapitalize="none"
          autoCorrect={false} value={remoteDir} onChangeText={setRemoteDir} />
        <Field label="Имя устройства" placeholder="как подписывать это устройство, например «Телефон Ани»"
          value={deviceName} onChangeText={setDeviceName} />
        <Text style={ui.mutedSmall}>
          Пароль приложения создаётся в Яндекс ID: Безопасность → Пароли приложений → «Файлы WebDAV».
          На телефоне он хранится в защищённом хранилище Android.
        </Text>
        <Button kind="primary" title="Сохранить" onPress={onSave} />
        <Button title="Синхронизировать" onPress={onSync} busy={app.syncBusy} />
        {status ? <Text style={ui.muted}>{status}</Text> : null}
      </Card>
      {app.configured ? (
        <Badge
          tone={app.dirty === false ? 'success' : 'warning'}
          text={app.dirty === null ? 'Яндекс.Диск: не удалось проверить изменения'
            : app.dirty ? 'Яндекс.Диск: есть невыложенные изменения' : 'Яндекс.Диск: невыложенных изменений нет'}
        />
      ) : null}
      <Text style={ui.mutedSmall}>
        Работайте по очереди: перед работой на телефоне база загружается с Диска, после правок — выкладывается.
        Если изменить базу и на компьютере, и на телефоне, приложение предложит выбрать одну версию.
      </Text>
    </Screen>
  );
}
