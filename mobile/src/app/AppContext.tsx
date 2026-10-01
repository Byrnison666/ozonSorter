/**
 * Состояние приложения: открытая база, настройки Диска, синхронизация.
 * Повторяет роль MainWindow ПК: release/reload базы вокруг обмена, обмен после
 * каждой правки, проверка при запуске; вместо «при закрытии» — тихая выгрузка
 * при уходе в фон.
 */
import {
  createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { ActivityIndicator, Alert, AppState, Modal, StyleSheet, Text, View } from 'react-native';

import type { Db } from '../core/db';
import { configureConnection, ensureSchema } from '../core/schema';
import { defaultConfig, isConfigured, type SyncConfig, targetId } from '../core/sync/config';
import {
  backgroundPush, type DbHost, hasLocalChanges, type InteractiveOptions, startupAction,
  syncInteractive, type SyncUi, texts, VPN_HINT,
} from '../core/sync/flow';
import { CancelToken, SyncCancelled, SyncError, SyncService } from '../core/sync/syncService';
import { fetchTransport, WebDavClient } from '../core/sync/webdav';
import { openExpoDb } from '../platform/expoDb';
import { DB_PATH, deviceDeps, loadConfig, saveConfig, sha256, STATE_PATH } from '../platform/deps';
import { probeInternet } from '../platform/io';
import { Button } from '../ui/components';
import { colors, radius, space } from '../ui/theme';

const CHECK_TIMEOUT_MS = 15_000;
const TRANSFER_TIMEOUT_MS = 300_000;
const SLOW_HINT_MS = 10_000;

export type Dirty = boolean | null;

interface AppApi {
  /** Открытая база; бросает, если её нет (идёт обмен или открыть не удалось). */
  db(): Db;
  dbError: string | null;
  /** Меняется при любой правке или замене базы — экраны перечитывают данные. */
  version: number;
  config: SyncConfig;
  saveSettings(c: SyncConfig): Promise<void>;
  configured: boolean;
  dirty: Dirty;
  syncBusy: boolean;
  syncNow(opts?: InteractiveOptions): Promise<boolean>;
  /** Экран изменил базу: перечитать и сразу выложить на Диск. */
  onDataChanged(): void;
  /** Долгая локальная операция под индикатором (без отмены). */
  runBusy<T>(text: string, fn: () => T | Promise<T>): Promise<T>;
  ask: typeof ask;
  message(title: string, text: string): Promise<void>;
}

const Ctx = createContext<AppApi | null>(null);

/**
 * Данные экрана из базы. Пока база закрыта (идёт обмен), отдаёт последние
 * прочитанные данные; error — текст, если прочитать не удалось.
 */
export function useDbQuery<T>(read: (db: Db) => T, deps: unknown[]): { data: T | undefined; error: string | null } {
  const { db, version } = useApp();
  const last = useRef<T | undefined>(undefined);
  return useMemo(() => {
    try {
      last.current = read(db());
      return { data: last.current, error: null };
    } catch (e) {
      return { data: last.current, error: (e as Error).message };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, db, ...deps]);
}

export function useApp(): AppApi {
  const api = useContext(Ctx);
  if (!api) throw new Error('useApp outside AppProvider');
  return api;
}

/** Вопрос с кнопками; системная «Назад» — escape. */
export function ask<T>(
  title: string, text: string,
  buttons: Array<{ text: string; value: T; style?: 'cancel' | 'destructive' | 'default' }>,
  escape: T,
): Promise<T> {
  return new Promise((resolve) => {
    Alert.alert(title, text, buttons.map((b) => ({ text: b.text, style: b.style, onPress: () => resolve(b.value) })), {
      cancelable: true, onDismiss: () => resolve(escape),
    });
  });
}

function messageBox(title: string, text: string): Promise<void> {
  return ask(title, text, [{ text: 'OK', value: undefined }], undefined);
}

interface Progress {
  text: string;
  cancel?: () => void;
  cancelling: boolean;
  slow: boolean;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const dbRef = useRef<Db | null>(null);
  const [dbError, setDbError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [config, setConfig] = useState<SyncConfig>(defaultConfig());
  const configRef = useRef(config);
  const [dirty, setDirty] = useState<Dirty>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const busyRef = useRef(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  // Отменённые операции ещё ждут сеть; новые не начинаем, пока они не закончатся.
  const abandoned = useRef(new Set<Promise<unknown>>());

  const host: DbHost = useMemo(() => ({
    release() {
      dbRef.current?.close();
      dbRef.current = null;
    },
    reload() {
      dbRef.current?.close();
      dbRef.current = null;
      try {
        const db = openExpoDb(DB_PATH);
        try {
          configureConnection(db);
          ensureSchema(db);
        } catch (e) {
          db.close();
          throw e;
        }
        dbRef.current = db;
        setDbError(null);
      } catch (e) {
        setDbError((e as Error).message);
        throw e;
      } finally {
        setVersion((v) => v + 1);
      }
    },
  }), []);

  const db = useCallback((): Db => {
    // Не переоткрываем здесь: на время обмена база закрыта намеренно.
    if (!dbRef.current) throw new Error('База сейчас недоступна.');
    return dbRef.current;
  }, []);

  const makeService = useCallback(async (c: SyncConfig) => new SyncService(deviceDeps, {
    dbPath: DB_PATH,
    client: new WebDavClient(c.url, c.login, c.password, fetchTransport, CHECK_TIMEOUT_MS),
    remoteDir: c.remoteDir,
    statePath: STATE_PATH,
    deviceName: c.deviceName,
    targetId: await targetId(c, sha256),
    transferTimeoutMs: TRANSFER_TIMEOUT_MS,
  }), []);

  const refreshDirty = useCallback(async () => {
    const c = configRef.current;
    if (!isConfigured(c)) return setDirty(null);
    try {
      setDirty(await hasLocalChanges(await makeService(c)));
    } catch {
      setDirty(null);
    }
  }, [makeService]);

  const withProgress = useCallback(async <T,>(text: string, fn: (t: CancelToken) => Promise<T>): Promise<T> => {
    if (abandoned.current.size) throw new SyncError('Предыдущая операция ещё завершается. Повторите через минуту.');
    const token = new CancelToken();
    const work = fn(token);
    return new Promise<T>((resolve, reject) => {
      let done = false;
      const slowTimer = setTimeout(() => setProgress((p) => (p ? { ...p, slow: true } : p)), SLOW_HINT_MS);
      const finish = () => {
        done = true;
        clearTimeout(slowTimer);
        setProgress(null);
      };
      setProgress({
        text, cancelling: false, slow: false,
        cancel: () => {
          if (done) return;
          if (token.cancel()) {
            finish();
            abandoned.current.add(work);
            work.catch(() => undefined).finally(() => abandoned.current.delete(work));
            reject(new SyncCancelled());
          } else {
            setProgress((p) => (p ? { ...p, cancelling: true, text: 'Завершается…' } : p));
          }
        },
      });
      work.then((v) => {
        if (!done) {
          finish();
          resolve(v);
        }
      }, (e) => {
        if (!done) {
          finish();
          reject(e);
        }
      });
    });
  }, []);

  const runBusy = useCallback(async <T,>(text: string, fn: () => T | Promise<T>): Promise<T> => {
    setProgress({ text, cancelling: false, slow: false });
    // Дать индикатору отрисоваться до синхронной работы (разбор xlsx и т.п.).
    await new Promise((r) => setTimeout(r, 50));
    try {
      return await fn();
    } finally {
      setProgress(null);
    }
  }, []);

  const ui: SyncUi = useMemo(() => ({
    withProgress,
    askConflict: (check) => (check.neverSynced
      ? ask('На Диске уже есть база', texts.conflictNeverSynced(check), [
        { text: 'Отмена', value: '' as const, style: 'cancel' },
        { text: 'Взять с Диска', value: 'remote' as const, style: 'destructive' },
      ], '' as const)
      : ask('Изменения с двух сторон', texts.conflict(check), [
      { text: 'Отмена', value: '' as const, style: 'cancel' },
      { text: 'Взять с Диска', value: 'remote' as const, style: 'destructive' },
      { text: 'Оставить эту', value: 'local' as const },
    ], '' as const)),
    askPull: (check) => ask(
      check.remoteIsOlder ? 'На Диске более старая база' : 'На Диске более новая база',
      texts.pull(check),
      [{ text: 'Нет', value: false, style: 'cancel' }, { text: 'Да', value: true }],
      false,
    ),
    askPushToEmptyDisk: () => ask('На Диске нет базы', texts.emptyDisk, [
      { text: 'Нет', value: false, style: 'cancel' }, { text: 'Да', value: true },
    ], false),
    askRetryOffline: (hasChanges, internetOk) => ask('Нет связи с Яндекс.Диском', texts.retryOffline(hasChanges, internetOk), [
      { text: 'Позже', value: false, style: 'cancel' }, { text: 'Повторить', value: true },
    ], false),
    showMessage: (_kind, title, text) => messageBox(title, text),
  }), [withProgress]);

  const syncNow = useCallback(async (opts: InteractiveOptions = {}) => {
    const c = configRef.current;
    if (!isConfigured(c) || busyRef.current) return false;
    busyRef.current = true;
    setSyncBusy(true);
    try {
      let service: SyncService;
      try {
        service = await makeService(c);
      } catch (e) {
        await messageBox('Синхронизация', `Адрес сервера в настройках не подходит: ${(e as Error).message}`);
        return false;
      }
      return await syncInteractive(service, host, ui, probeInternet, opts);
    } finally {
      busyRef.current = false;
      setSyncBusy(false);
      void refreshDirty();
    }
  }, [host, makeService, refreshDirty, ui]);

  const onDataChanged = useCallback(() => {
    setVersion((v) => v + 1);
    void syncNow({ confirmPull: true });
  }, [syncNow]);

  const saveSettings = useCallback(async (c: SyncConfig) => {
    await saveConfig(c);
    configRef.current = c;
    setConfig(c);
    await refreshDirty();
  }, [refreshDirty]);

  // Запуск: открыть базу, прочитать настройки, проверить Диск в фоне.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return undefined;
    started.current = true;
    let alive = true;
    try {
      host.reload();
    } catch {
      // ошибка показана через dbError
    }
    (async () => {
      const c = await loadConfig();
      if (!alive) return;
      configRef.current = c;
      setConfig(c);
      if (!isConfigured(c)) return;
      void refreshDirty();
      let action;
      try {
        action = await startupAction(await makeService(c));
      } catch {
        return; // адрес не https — синхронизация выключена до исправления настроек
      }
      if (!alive) return;
      if (action.kind === 'warn') await messageBox('Синхронизация', action.text);
      else if (action.kind === 'sync') {
        await syncNow({ confirmPull: true, announce: action.announce, assumeOffline: action.assumeOffline });
      }
    })();
    return () => {
      alive = false;
    };
  }, [host, makeService, refreshDirty, syncNow]);

  // Уход в фон вместо «закрытия программы»: тихо выложить, если можно.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state !== 'background' || busyRef.current) return;
      const c = configRef.current;
      if (!isConfigured(c)) return;
      busyRef.current = true;
      (async () => {
        try {
          await backgroundPush(await makeService(c), host);
        } finally {
          busyRef.current = false;
          void refreshDirty();
        }
      })();
    });
    return () => sub.remove();
  }, [host, makeService, refreshDirty]);

  const api: AppApi = {
    db, dbError, version, config, saveSettings, configured: isConfigured(config), dirty, syncBusy,
    syncNow, onDataChanged, runBusy, ask, message: messageBox,
  };

  return (
    <Ctx.Provider value={api}>
      {children}
      <Modal visible={progress !== null} transparent animationType="fade" onRequestClose={() => progress?.cancel?.()}>
        <View style={styles.backdrop}>
          <View style={styles.dialog} accessibilityLiveRegion="polite">
            <Text style={styles.dialogText}>{progress?.text}</Text>
            <ActivityIndicator size="large" color={colors.primary} />
            {progress?.slow ? <Text style={styles.hint}>Связь с Яндекс.Диском медленная. {VPN_HINT}</Text> : null}
            {progress?.cancel ? (
              <Button title="Отмена" onPress={() => progress.cancel?.()} disabled={progress.cancelling} />
            ) : null}
          </View>
        </View>
      </Modal>
    </Ctx.Provider>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: 'rgba(15,23,42,0.45)', justifyContent: 'center', padding: space.xl },
  dialog: { backgroundColor: colors.surface, borderRadius: radius.lg, padding: space.xl, gap: space.lg },
  dialogText: { fontSize: 16, color: colors.text, textAlign: 'center' },
  hint: { fontSize: 13, color: colors.textSecondary, textAlign: 'center', lineHeight: 18 },
});
