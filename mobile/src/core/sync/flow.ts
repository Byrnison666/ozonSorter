/**
 * Сценарий синхронизации для интерфейса — перенос src/ui/sync_flow.py и
 * логики запуска/правок из main_window.py ПК. Диалоги — за интерфейсом SyncUi,
 * поэтому сценарий проверяется тестами без экрана.
 */
import {
  CancelToken, RemoteDataError, SyncCancelled, type SyncCheck, SyncError, SyncService, SyncStatus,
} from './syncService';
import { type Progress, YaDiskError } from './yadisk';
import { OAuthError } from './yandexOAuth';

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
  /**
   * Выполнить fn под модальным индикатором с «Отменой». Отмена — SyncCancelled.
   * Через onProgress fn сообщает ход передачи файла — индикатор показывает
   * объём, долю и скорость.
   */
  withProgress<T>(text: string, fn: (token: CancelToken, onProgress: Progress) => Promise<T>): Promise<T>;
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

const WORK_LOCALLY = 'База сохранена на этом телефоне, работать можно — изменения выложатся, когда Диск заработает.';

/**
 * Сбой Диска, который проходит сам: недоступность либо испорченный ответ
 * (так выглядят сбои в дата-центрах Яндекса). Тихие попытки считают его
 * недоступностью: отметка, пауза автосинхронизации, без окон.
 */
export function isDiskGlitch(error: unknown): boolean {
  return (error instanceof YaDiskError && (error.isUnavailable || error.isBadResponse))
    || error instanceof RemoteDataError;
}

export function describeError(error: unknown): string {
  if (error instanceof YaDiskError) {
    if (error.isAuthError) {
      return 'Яндекс.Диск не принял вход: доступ отозван или истёк. Войдите в Яндекс заново в «Настройках».';
    }
    if (error.isTlsError) {
      return 'Не удалось проверить защищённое соединение с Яндекс.Диском. Проверьте дату и время на телефоне.';
    }
    if (error.isNetworkError) return `Нет связи с Яндекс.Диском. Проверьте интернет и повторите.\n${VPN_HINT}`;
    if (error.status === 507) return 'На Яндекс.Диске закончилось место.';
    if (error.isUnavailable) {
      return `Яндекс.Диск сейчас не работает (сбой на стороне Яндекса, код ${error.status}).\n${WORK_LOCALLY}`;
    }
    if (error.isBadResponse) return `Яндекс.Диск прислал непонятный ответ (сбой на стороне Яндекса).\n${WORK_LOCALLY}`;
    return `Яндекс.Диск ответил ошибкой ${error.status}. Повторите позже.`;
  }
  if (error instanceof RemoteDataError) return `${error.message}\n${WORK_LOCALLY}`;
  if (error instanceof SyncError) return error.message;
  return `Не удалось выполнить синхронизацию:\n${(error as Error)?.message ?? String(error)}`;
}

/** Текст ошибки входа в Яндекс — как describe_login_error ПК. */
export function describeLoginError(error: unknown): string {
  if (error instanceof OAuthError) {
    if (error.code === 'expired_token') return 'Код устарел. Нажмите «Войти в Яндекс» ещё раз.';
    if (error.code === 'access_denied') return 'Вход отклонён на странице Яндекса.';
    if (error.code === 'not_configured') return 'В этой сборке приложения не настроен вход в Яндекс.';
    return `Яндекс не выполнил вход (${error.code || error.message}). Повторите позже.`;
  }
  if (error instanceof YaDiskError) {
    if (error.isTlsError) {
      return 'Не удалось проверить защищённое соединение с Яндексом. Проверьте дату и время на телефоне.';
    }
    if (error.isNetworkError) return 'Нет связи с Яндексом. Проверьте интернет (и отключите VPN) и повторите.';
    return `Яндекс ответил ошибкой ${error.status}. Повторите позже.`;
  }
  return `Не удалось войти в Яндекс:\n${(error as Error)?.message ?? String(error)}`;
}

function describeRemote(check: SyncCheck): string {
  const meta = check.meta!;
  return `устройство «${meta.device || '—'}», сохранена ${formatTime(meta.saved_at)}`;
}

/** Размер для оператора: «850 КБ», «3,4 МБ» — как на ПК. */
export function formatSize(size: number): string {
  if (size < 1024 * 1024) return `${Math.round(Math.max(size, 0) / 1024)} КБ`;
  return `${(size / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
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
 * Бросает YaDiskError, SyncError, SyncCancelled.
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
      const rev = await ui.withProgress('Выгрузка базы на Яндекс.Диск…', (t, p) => service.push(check.meta, t, p));
      return { outcome: 'PUSHED', text: `Изменения выложены на Яндекс.Диск (ревизия ${rev}).` };
    }

    const rev = await ui.withProgress('Загрузка базы с Яндекс.Диска…', (t, p) => service.pull(check.meta, t, p));
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
  /**
   * Недоступность Диска без окон (автоматические попытки): база остаётся на
   * телефоне, изменения выложит следующая удачная синхронизация.
   */
  quiet?: boolean;
}

/**
 * Итог синхронизации со всеми диалогами: IN_SYNC — база совпадает с Диском;
 * NOT_SYNCED — отказ, отмена или ошибка (показана); UNAVAILABLE — Диск
 * временно недоступен, работаем с базой телефона.
 */
export type SyncAttempt = 'IN_SYNC' | 'NOT_SYNCED' | 'UNAVAILABLE';

/**
 * Синхронизация со всеми диалогами. Без интернета предлагает повторить; после
 * повтора об успехе сообщается всегда. При сбое на стороне Яндекса объясняет,
 * что работать можно.
 */
export async function syncInteractive(
  service: SyncService, host: DbHost, ui: SyncUi, probeInternet: () => Promise<boolean>,
  { announce = false, confirmPull = false, quiet = false }: InteractiveOptions = {},
): Promise<SyncAttempt> {
  const probe = async () => {
    try {
      return await ui.withProgress('Проверка связи…', () => probeInternet());
    } catch {
      return null;
    }
  };
  let wasOffline = false;
  for (;;) {
    let result: SyncResult;
    try {
      result = await synchronize(service, host, ui, confirmPull);
    } catch (e) {
      if (e instanceof SyncCancelled) return 'NOT_SYNCED';
      if (quiet && isDiskGlitch(e)) {
        console.warn('Quiet sync: Disk is unavailable', e);
        return 'UNAVAILABLE';
      }
      if (e instanceof YaDiskError && e.isNetworkError) {
        if (await ui.askRetryOffline((await hasLocalChanges(service)) === true, await probe())) {
          wasOffline = true;
          continue;
        }
        return 'UNAVAILABLE';
      }
      if (isDiskGlitch(e)) {
        await ui.showMessage('warning', 'Яндекс.Диск недоступен', describeError(e));
        return 'UNAVAILABLE';
      }
      await ui.showMessage('error', 'Ошибка синхронизации', describeError(e));
      return 'NOT_SYNCED';
    }
    if (inSync(result) && (announce || wasOffline)) await ui.showMessage('info', 'Синхронизация', result.text);
    return inSync(result) ? 'IN_SYNC' : 'NOT_SYNCED';
  }
}

/** Что сделать после фоновой проверки при запуске (_handle_startup_result ПК). */
export type StartupAction =
  | { kind: 'none' }
  | { kind: 'unavailable' }
  | { kind: 'warn'; text: string }
  | { kind: 'sync'; announce: boolean };

export async function startupAction(service: SyncService): Promise<StartupAction> {
  let check: SyncCheck;
  try {
    check = await service.check();
  } catch (e) {
    // Работаем с базой телефона без окон; изменения выложит следующая удачная синхронизация.
    if (isDiskGlitch(e)) {
      console.warn('Startup check: Disk is unavailable', e);
      return { kind: 'unavailable' };
    }
    return { kind: 'warn', text: describeError(e) };
  }
  if (check.status === SyncStatus.UP_TO_DATE) return { kind: 'none' };
  // Невыложенные с прошлого раза изменения: об успехе сообщаем.
  return { kind: 'sync', announce: check.status === SyncStatus.LOCAL_AHEAD };
}

/**
 * Приложение уходит в фон: окна показать нельзя, поэтому только тихая выгрузка,
 * когда Диск не менялся (LOCAL_AHEAD). Конфликт и загрузку решит пользователь,
 * когда вернётся. Возвращает номер выложенной ревизии; 'UNAVAILABLE' — Диск
 * недоступен; null — выкладывать нечего или нельзя без вопросов.
 *
 * Обмен идёт со снимком базы: основная база закрыта только на время его
 * копирования, onSnapshot — она снова открыта и с ней можно работать, пока
 * снимок уходит на Диск. Состояние синхронизации запоминает сумму снимка,
 * поэтому правки, сделанные во время выгрузки, остаются невыложенными.
 * Снимок — побайтная копия, а не VACUUM INTO (тот не закрывал бы базу и на
 * миг): VACUUM пересобирает файл, и сумма копии отличалась бы от суммы
 * рабочей базы — после каждой фоновой выгрузки база выглядела бы изменённой
 * и уходила бы на Диск лишней ревизией.
 */
export async function backgroundPush(
  service: SyncService, host: DbHost, onSnapshot?: () => void,
): Promise<number | 'UNAVAILABLE' | null> {
  let snapshot: { service: SyncService; discard(): Promise<void> } | null = null;
  try {
    if ((await hasLocalChanges(service)) !== true) return null;
    host.release();
    try {
      snapshot = await service.snapshot();
    } finally {
      host.reload();
    }
    onSnapshot?.();
    const check = await snapshot.service.check();
    if (check.status !== SyncStatus.LOCAL_AHEAD) return null;
    return await snapshot.service.push(check.meta);
  } catch (e) {
    console.warn('Background push failed', e);
    return isDiskGlitch(e) ? 'UNAVAILABLE' : null;
  } finally {
    await snapshot?.discard().catch((e) => console.warn('Database snapshot was not removed', e));
  }
}

/**
 * Обменяться сразу после фоновой выгрузки: правки, сделанные во время неё,
 * иначе ждали бы следующего повода. Не при открытом выборе файла или
 * «Поделиться»: экспорт ждёт там между файлом и отметкой посылок, и обмен,
 * заменив базу, оставил бы отметку не в той базе. Импорт и экспорт по
 * окончании сами запускают обмен.
 */
export function syncAfterBackground(s: {
  changed: boolean; active: boolean; externalOpen: number; now: number; autoSyncAfter: number;
}): boolean {
  return s.changed && s.active && s.externalOpen === 0 && s.now >= s.autoSyncAfter;
}

/** Скорость, ниже которой передача считается медленной (байт/с), — как на ПК. */
export const SLOW_SPEED = 64 * 1024;
const SPEED_WINDOW_MS = 3000;

/**
 * Скорость передачи за последние 3 с по отчётам onProgress. Пока данных
 * мало — 0.
 */
export class SpeedMeter {
  private samples: Array<[number, number]> = [];

  add(done: number, now = Date.now()): void {
    this.samples.push([now, done]);
    while (this.samples.length > 2 && now - this.samples[0][0] > SPEED_WINDOW_MS) this.samples.shift();
  }

  get hasData(): boolean {
    return this.samples.length > 0;
  }

  /** Байт в секунду. */
  get speed(): number {
    if (this.samples.length < 2) return 0;
    const [t0, d0] = this.samples[0];
    const [t1, d1] = this.samples[this.samples.length - 1];
    return t1 > t0 ? ((d1 - d0) * 1000) / (t1 - t0) : 0;
  }
}

/** «1,2 МБ из 3,4 МБ · 450 КБ/с»; total 0 — размер неизвестен. */
export function transferText(done: number, total: number, speed: number): string {
  let text = total ? `${formatSize(done)} из ${formatSize(total)}` : formatSize(done);
  if (speed > 0) text += ` · ${formatSize(speed)}/с`;
  return text;
}
