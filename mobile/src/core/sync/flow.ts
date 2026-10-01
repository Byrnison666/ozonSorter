/**
 * Сценарий синхронизации для интерфейса — перенос src/ui/sync_flow.py и
 * логики запуска/правок из main_window.py ПК. Диалоги — за интерфейсом SyncUi,
 * поэтому сценарий проверяется тестами без экрана.
 */
import {
  CancelToken, SyncCancelled, type SyncCheck, SyncError, SyncService, SyncStatus,
} from './syncService';
import { WebDavError } from './webdav';

// Через VPN Яндекс.Диск обычно недоступен: запросы либо падают, либо висят.
export const VPN_HINT =
  'Если на телефоне включён VPN, отключите его: с включённым VPN Яндекс.Диск обычно недоступен.';

export type SyncOutcome = 'UNCHANGED' | 'PUSHED' | 'PULLED' | 'DECLINED';

export interface SyncResult {
  outcome: SyncOutcome;
  text: string;
}

export const inSync = (r: SyncResult) => r.outcome !== 'DECLINED';

export interface SyncUi {
  /** Выполнить fn под модальным индикатором с «Отменой». Отмена — SyncCancelled. */
  withProgress<T>(text: string, fn: (token: CancelToken) => Promise<T>): Promise<T>;
  /** Какую версию оставить: 'local', 'remote' или '' (отмена). */
  askConflict(check: SyncCheck): Promise<'local' | 'remote' | ''>;
  askPull(check: SyncCheck): Promise<boolean>;
  askPushToEmptyDisk(): Promise<boolean>;
  /** true — «Повторить». internetOk: есть ли интернет вообще (null — неизвестно). */
  askRetryOffline(hasLocalChanges: boolean, internetOk: boolean | null): Promise<boolean>;
  showMessage(kind: 'info' | 'warning' | 'error', title: string, text: string): Promise<void>;
}

/** Основная база приложения: закрыть перед обменом, открыть после. */
export interface DbHost {
  release(): void;
  /** Открыть заново; бросает, если файл не открывается. */
  reload(): void;
}

export function describeError(error: unknown): string {
  if (error instanceof WebDavError) {
    if (error.isAuthError) {
      return 'Яндекс.Диск не принял логин или пароль приложения. Проверьте их в «Настройках».';
    }
    if (error.isNetworkError) return `Нет связи с Яндекс.Диском. Проверьте интернет и повторите.\n${VPN_HINT}`;
    if (error.status === 507) return 'На Яндекс.Диске закончилось место.';
    return `Яндекс.Диск ответил ошибкой ${error.status}. Повторите позже.`;
  }
  if (error instanceof SyncError) return error.message;
  return `Не удалось выполнить синхронизацию:\n${(error as Error)?.message ?? String(error)}`;
}

function describeRemote(check: SyncCheck): string {
  const meta = check.meta!;
  return `устройство «${meta.device || '—'}», сохранена ${formatTime(meta.saved_at)}`;
}

/** '2026-09-30 14:05:00' → '30.09.2026 14:05'; пустое — '—'. */
export function formatTime(value: string): string {
  const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)/.exec(value);
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : '—';
}

/** Тексты окон — как на ПК. */
export const texts = {
  conflict(check: SyncCheck): string {
    const older = check.remoteIsOlder
      ? '\nВнимание: версия на Диске не продолжает ту, с которой это устройство работало ' +
        '(номер меньше либо версию заменили другой базой).\n'
      : '';
    return 'База изменена и на этом телефоне, и на Яндекс.Диске.\n\n' +
      `Версия этого телефона: последняя синхронизация ${formatTime(check.localSyncedAt)}, ` +
      'после неё есть изменения.\n' +
      `Версия на Диске: ${describeRemote(check)}.\n${older}\n` +
      'Объединить их приложение не может. Какую версию оставить?\n\n' +
      '«Оставить эту» — база телефона станет текущей на Диске; версия с Диска останется ' +
      'там среди пяти последних.\n' +
      '«Взять с Диска» — база телефона заменится; прежняя сохранится в резервной копии.';
  },
  /** Конфликт на устройстве, ни разу не загружавшем базу: заменить Диск нельзя. */
  conflictNeverSynced(check: SyncCheck): string {
    return 'На Яндекс.Диске уже есть база, а этот телефон ещё ни разу её не загружал.\n' +
      `Версия на Диске: ${describeRemote(check)}.\n\n` +
      'Заменить базу на Диске базой телефона нельзя — так можно по ошибке стереть все данные.\n\n' +
      '«Взять с Диска» — база телефона заменится; прежняя сохранится в резервной копии.';
  },
  pull(check: SyncCheck): string {
    if (check.remoteIsOlder) {
      return 'На Яндекс.Диске лежит база, которая не продолжает ту, с которой этот телефон ' +
        `работал (${describeRemote(check)}): номер версии меньше либо та же версия заменена ` +
        'другой базой.\nТак бывает, если папку на Диске восстановили из старой копии или два ' +
        'устройства выложили базу одновременно.\n\nЗаменить базу телефона ею?';
    }
    return `На Яндекс.Диске есть более новая база (${describeRemote(check)}).\nЗагрузить её?`;
  },
  emptyDisk:
    'Этот телефон уже синхронизировался с Яндекс.Диском, но сейчас базы там нет: папку ' +
    'удалили или очистили.\n\nВыложить базу телефона заново?',
  retryOffline(hasLocalChanges: boolean, internetOk: boolean | null): string {
    const head = hasLocalChanges
      ? 'Изменения сохранены на телефоне, но на Яндекс.Диск не выложены.'
      : 'Не удалось связаться с Яндекс.Диском.';
    const reason = internetOk === true
      ? 'Интернет на телефоне есть, а Яндекс.Диск недоступен.'
      : internetOk === false
        ? 'Нет связи с интернетом. Проверьте подключение.'
        : 'Нет связи с Яндекс.Диском.';
    return `${head}\n\n${reason}\n${VPN_HINT}\n\nНажмите «Повторить», когда связь появится.`;
  },
};

/**
 * Один проход: проверить Диск и выложить либо загрузить базу. confirmPull —
 * спрашивать перед загрузкой (автоматические запуски).
 * Бросает WebDavError, SyncError, SyncCancelled.
 */
export async function synchronize(
  service: SyncService, host: DbHost, ui: SyncUi, confirmPull = false,
): Promise<SyncResult> {
  host.release();
  let reloaded = false;
  try {
    const check = await ui.withProgress('Проверка Яндекс.Диска…', () => service.check());
    let status = check.status;

    if (status === SyncStatus.UP_TO_DATE) return { outcome: 'UNCHANGED', text: 'База совпадает с Яндекс.Диском.' };
    if (status === SyncStatus.CONFLICT) {
      const choice = await ui.askConflict(check);
      if (!choice) return { outcome: 'DECLINED', text: 'Версии различаются, выбор отложен.' };
      status = choice === 'local' ? SyncStatus.LOCAL_AHEAD : SyncStatus.REMOTE_AHEAD;
    } else if (status === SyncStatus.REMOTE_MISSING) {
      if (!(await ui.askPushToEmptyDisk())) return { outcome: 'DECLINED', text: 'На Диске нет базы, выгрузка отложена.' };
      status = SyncStatus.LOCAL_AHEAD;
    } else if (status === SyncStatus.REMOTE_AHEAD && (confirmPull || check.remoteIsOlder)) {
      if (!(await ui.askPull(check))) return { outcome: 'DECLINED', text: 'Загрузка с Диска отложена.' };
    }

    if (status === SyncStatus.LOCAL_AHEAD) {
      const rev = await ui.withProgress('Выгрузка базы на Яндекс.Диск…', (t) => service.push(check.meta, t));
      return { outcome: 'PUSHED', text: `Изменения выложены на Яндекс.Диск (ревизия ${rev}).` };
    }

    const rev = await ui.withProgress('Загрузка базы с Яндекс.Диска…', (t) => service.pull(check.meta, t));
    try {
      host.reload();
      reloaded = true;
    } catch (e) {
      // Загруженная база не открылась — возвращаем прежнюю.
      host.release();
      try {
        await service.rollbackPull();
      } catch {
        throw new SyncError(
          'База с Диска не открылась, и прежнюю базу вернуть не удалось. ' +
            'Её копия лежит в резервных копиях приложения (backups/presync).',
        );
      }
      host.reload();
      reloaded = true;
      throw new SyncError(`База с Диска не открылась в приложении, возвращена прежняя база телефона. (${(e as Error).message})`);
    }
    return { outcome: 'PULLED', text: `База загружена с Яндекс.Диска (ревизия ${rev}).` };
  } finally {
    if (!reloaded) {
      try {
        host.reload();
      } catch (e) {
        // Не заглушаем исходную ошибку обмена; экран покажет, что база не открыта.
        console.warn('Database reopen after sync failed', e);
      }
    }
  }
}

/** Есть ли невыложенные правки. null — проверить не удалось (это не «нет»). */
export async function hasLocalChanges(service: SyncService): Promise<boolean | null> {
  try {
    return await service.localChanged();
  } catch {
    return null;
  }
}

export interface InteractiveOptions {
  /** Сообщать об успехе и без сбоев (ручной запуск). */
  announce?: boolean;
  confirmPull?: boolean;
  /** Отсутствие связи уже установлено: сразу предложить повторить. */
  assumeOffline?: boolean;
}

/**
 * Синхронизация со всеми диалогами; true — база совпадает с Диском. Без
 * интернета предлагает повторить; после повтора об успехе сообщается всегда.
 */
export async function syncInteractive(
  service: SyncService, host: DbHost, ui: SyncUi, probeInternet: () => Promise<boolean>,
  { announce = false, confirmPull = false, assumeOffline = false }: InteractiveOptions = {},
): Promise<boolean> {
  const probe = async () => {
    try {
      return await ui.withProgress('Проверка связи…', () => probeInternet());
    } catch {
      return null;
    }
  };
  let wasOffline = false;
  if (assumeOffline) {
    if (!(await ui.askRetryOffline((await hasLocalChanges(service)) === true, await probe()))) return false;
    wasOffline = true;
  }
  for (;;) {
    let result: SyncResult;
    try {
      result = await synchronize(service, host, ui, confirmPull);
    } catch (e) {
      if (e instanceof SyncCancelled) return false;
      if (e instanceof WebDavError && e.isNetworkError) {
        if (await ui.askRetryOffline((await hasLocalChanges(service)) === true, await probe())) {
          wasOffline = true;
          continue;
        }
        return false;
      }
      await ui.showMessage('error', 'Ошибка синхронизации', describeError(e));
      return false;
    }
    if (inSync(result) && (announce || wasOffline)) await ui.showMessage('info', 'Синхронизация', result.text);
    return inSync(result);
  }
}

/** Что сделать после фоновой проверки при запуске (_handle_startup_result ПК). */
export type StartupAction =
  | { kind: 'none' }
  | { kind: 'warn'; text: string }
  | { kind: 'sync'; announce: boolean; assumeOffline: boolean };

export async function startupAction(service: SyncService): Promise<StartupAction> {
  let check: SyncCheck;
  try {
    check = await service.check();
  } catch (e) {
    if (e instanceof WebDavError && e.isNetworkError) {
      // Без интернета молчим, пока выкладывать нечего.
      return (await hasLocalChanges(service)) === true
        ? { kind: 'sync', announce: false, assumeOffline: true }
        : { kind: 'none' };
    }
    return { kind: 'warn', text: describeError(e) };
  }
  if (check.status === SyncStatus.UP_TO_DATE) return { kind: 'none' };
  // Невыложенные с прошлого раза изменения: об успехе сообщаем.
  return { kind: 'sync', announce: check.status === SyncStatus.LOCAL_AHEAD, assumeOffline: false };
}

/**
 * Приложение уходит в фон: окна показать нельзя, поэтому только тихая выгрузка,
 * когда Диск не менялся (LOCAL_AHEAD). Конфликт и загрузку решит пользователь,
 * когда вернётся. Возвращает номер выложенной ревизии или null.
 */
export async function backgroundPush(service: SyncService, host: DbHost): Promise<number | null> {
  try {
    if ((await hasLocalChanges(service)) !== true) return null;
    host.release();
    try {
      const check = await service.check();
      if (check.status !== SyncStatus.LOCAL_AHEAD) return null;
      return await service.push(check.meta);
    } finally {
      host.reload();
    }
  } catch (e) {
    console.warn('Background push failed', e);
    return null;
  }
}
