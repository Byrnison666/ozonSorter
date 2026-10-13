/**
 * Клиент REST API Яндекс.Диска для синхронизации — перенос src/yadisk_api.py.
 * Все пути — внутри папки приложения (app:/ = «Приложения/OzonSorter»):
 * другого доступа у токена нет.
 *
 * Почему не WebDAV: Яндекс искусственно замедляет выгрузку по WebDAV. REST API
 * тоже замедляет файлы с архивными расширениями (.gz), поэтому большой файл
 * выгружается под временным именем и переименовывается на сервере.
 *
 * Сетевой обмен вынесен в Transport: на телефоне — XMLHttpRequest React Native
 * (он, в отличие от fetch, сообщает ход выгрузки и скачивания), в тестах — fetch Bun.
 */
import { strFromU8 } from 'fflate';

export const API_URL = 'https://cloud-api.yandex.net/v1/disk';
export const DEFAULT_MAX_BYTES = 8 * 1024 * 1024;
const MAX_JSON_BYTES = 1024 * 1024;
// Файлы не больше этого выгружаются сразу под своим именем: замедление по
// расширению на них не сказывается, а переименование — лишний запрос.
const DIRECT_UPLOAD_MAX = 64 * 1024;
const TEMP_SUFFIX = '.tmp';
const POLL_DELAYS_MS = [300, 500, 1000, 1000, 2000];
/** Паузы перед повторами запроса на чтение, на который Диск ответил временным сбоем. */
export const RETRY_DELAYS_MS: readonly number[] = [1000, 2000];
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '[::1]']);

/** (передано байт, всего байт); всего 0 — размер неизвестен. */
export type Progress = (done: number, total: number) => void;

/**
 * Сбой на стороне Яндекса, который проходит сам: 5xx и «слишком много
 * запросов». 507 (кончилось место) сам не пройдёт.
 */
function isTransient(status: number): boolean {
  return status === 429 || (status >= 500 && status < 600 && status !== 507);
}

export class YaDiskError extends Error {
  constructor(
    message: string,
    /** Код HTTP; null — сбой соединения. */
    readonly status: number | null = null,
    readonly isTlsError = false,
  ) {
    super(message);
  }

  /** 401 — токен отозван или истёк, 403 — у токена нет нужного права. */
  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403;
  }

  /** Нет связи. Ошибку сертификата повтор «когда появится интернет» не лечит. */
  get isNetworkError(): boolean {
    return this.status === null && !this.isTlsError;
  }

  /**
   * Диск временно недоступен: нет связи или сбой на стороне Яндекса. Пройдёт
   * само — с базой можно работать локально и выложить её позже.
   */
  get isUnavailable(): boolean {
    return this.isNetworkError || (this.status !== null && isTransient(this.status));
  }
}

export interface HttpRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body?: Uint8Array;
  timeoutMs: number;
  maxBytes: number;
  /** Ход передачи тела запроса (если оно есть) либо ответа. */
  onProgress?: Progress;
}

export interface HttpResponse {
  status: number;
  body: Uint8Array;
  /** Адрес после переадресаций. */
  finalUrl: string;
}

/**
 * Сетевой обмен. Переадресации выполняет сам (fetch и XHR иначе не умеют);
 * сбой соединения — YaDiskError со status null.
 */
export interface Transport {
  request(req: HttpRequest): Promise<HttpResponse>;
}

function splitUrl(url: string): { scheme: string; host: string; origin: string } {
  const m = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/([^/?#]*)/.exec(url);
  if (!m) return { scheme: '', host: '', origin: '' };
  const authority = m[2].replace(/^[^@]*@/, '');
  const host = authority.startsWith('[')
    ? authority.slice(0, authority.indexOf(']') + 1)
    : authority.replace(/:\d*$/, '');
  return { scheme: m[1].toLowerCase(), host: host.toLowerCase(), origin: `${m[1]}://${authority}`.toLowerCase() };
}

/** https либо http на локальный адрес (тестовый сервер). */
export function isSecureUrl(url: string): boolean {
  const { scheme, host } = splitUrl(url);
  return scheme === 'https' || (scheme === 'http' && LOOPBACK_HOSTS.has(host));
}

/** Тело как JSON-объект; не JSON или не объект — пустой объект. */
function parseJson(body: Uint8Array): Record<string, unknown> {
  try {
    const data: unknown = JSON.parse(strFromU8(body));
    return data !== null && typeof data === 'object' && !Array.isArray(data)
      ? data as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function formEncode(fields: Record<string, string>): string {
  return Object.entries(fields).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Запрос с проверками, общими для всех: незащищённый адрес не открываем,
 * переадресация на незащищённый адрес — отказ, ответ не больше maxBytes.
 */
export async function httpRequest(transport: Transport, req: HttpRequest): Promise<HttpResponse & { json(): Record<string, unknown> }> {
  if (!isSecureUrl(req.url)) throw new YaDiskError(`Refusing insecure URL for ${req.method}`);
  const response = await transport.request(req);
  if (!isSecureUrl(response.finalUrl)) {
    throw new YaDiskError(`${req.method}: redirect to insecure URL refused`, 0);
  }
  if (response.body.length > req.maxBytes) {
    throw new YaDiskError(`${req.method} response is larger than ${req.maxBytes} bytes`, response.status);
  }
  return { ...response, json: () => parseJson(response.body) };
}

type Json = Record<string, unknown>;

export class YaDiskClient {
  readonly apiUrl: string;

  constructor(
    /** Меняется при обновлении токена. */
    public token: string,
    apiUrl: string,
    private readonly transport: Transport,
    readonly timeoutMs = 30_000,
    private readonly retryDelaysMs: readonly number[] = RETRY_DELAYS_MS,
  ) {
    // Токен уходит в каждом запросе — без TLS его видно в сети.
    if (!isSecureUrl(apiUrl)) throw new Error('Disk API URL must use https');
    this.apiUrl = apiUrl.replace(/\/+$/, '');
  }

  // --- служебное ---

  private static diskPath(path: string): string {
    return `app:/${path.split('/').filter(Boolean).join('/')}`;
  }

  private async api(
    method: string, endpoint: string, params?: Record<string, string>, url?: string,
  ): Promise<{ status: number; json: Json }> {
    const target = url ?? `${this.apiUrl}${endpoint}${params ? `?${formEncode(params)}` : ''}`;
    // Токен — только своему API: адрес операции приходит в ответе сервера.
    if (splitUrl(target).origin !== splitUrl(this.apiUrl).origin) {
      throw new YaDiskError(`${method} ${endpoint}: unexpected API host`);
    }
    const send = () => httpRequest(this.transport, {
      method, url: target, timeoutMs: this.timeoutMs, maxBytes: MAX_JSON_BYTES,
      headers: { Authorization: `OAuth ${this.token}`, Accept: 'application/json' },
    });
    let response = await send();
    // Повторяем только чтение: запись сервер мог успеть выполнить, и повтор
    // дал бы ложную ошибку. Несостоявшуюся выгрузку повторит следующая
    // синхронизация — meta.json пишется последним.
    if (method === 'GET') {
      for (const delay of this.retryDelaysMs) {
        if (!isTransient(response.status)) break;
        await sleep(delay);
        response = await send();
      }
    }
    return { status: response.status, json: response.json() };
  }

  private static fail(method: string, what: string, status: number, json: Json = {}): YaDiskError {
    const error = typeof json.error === 'string' ? ` ${json.error}` : '';
    return new YaDiskError(`${method} ${what} failed with HTTP ${status}${error}`, status);
  }

  /** Дождаться фоновой операции Диска (перенос, копирование). */
  private async wait(href: string, timeoutMs?: number): Promise<void> {
    const deadline = Date.now() + (timeoutMs ?? this.timeoutMs);
    for (let attempt = 0; ; attempt++) {
      const { status, json } = await this.api('GET', '/operations', undefined, href);
      if (status !== 200) throw YaDiskClient.fail('GET', 'operation', status, json);
      if (json.status === 'success') return;
      if (json.status !== 'in-progress') {
        throw new YaDiskError(`Disk operation finished with status ${String(json.status)}`, 500);
      }
      if (Date.now() > deadline) throw new YaDiskError('Disk operation timed out');
      await sleep(POLL_DELAYS_MS[Math.min(attempt, POLL_DELAYS_MS.length - 1)]);
    }
  }

  /** 201 — готово; 202 — Диск выполняет операцию в фоне, дожидаемся. */
  private async finish(method: string, what: string, r: { status: number; json: Json }, timeoutMs?: number): Promise<void> {
    if (r.status === 202) {
      if (typeof r.json.href !== 'string') throw YaDiskClient.fail(method, what, r.status, r.json);
      await this.wait(r.json.href, timeoutMs);
    } else if (r.status !== 200 && r.status !== 201) {
      throw YaDiskClient.fail(method, what, r.status, r.json);
    }
  }

  private async upload(path: string, data: Uint8Array, timeoutMs?: number, onProgress?: Progress): Promise<void> {
    const link = await this.api('GET', '/resources/upload', { path: YaDiskClient.diskPath(path), overwrite: 'true' });
    const { href, method = 'PUT', operation_id: operationId } = link.json;
    if (link.status !== 200 || typeof href !== 'string' || (method !== 'PUT' && method !== 'POST')) {
      throw YaDiskClient.fail('GET', `upload link for ${path}`, link.status, link.json);
    }
    // Ссылка на выгрузку уже подписана — токен на чужой хост не отправляем.
    const uploaded = await httpRequest(this.transport, {
      method, url: href, body: data, headers: { 'Content-Type': 'application/octet-stream' },
      timeoutMs: timeoutMs ?? this.timeoutMs, maxBytes: MAX_JSON_BYTES, onProgress,
    });
    if (uploaded.status === 202 && typeof operationId === 'string') {
      // Файл принят, но Диск ещё не положил его на место.
      await this.wait(`${this.apiUrl}/operations/${encodeURIComponent(operationId)}`, timeoutMs);
    } else if (![200, 201, 202].includes(uploaded.status)) {
      throw YaDiskClient.fail(method, `upload of ${path}`, uploaded.status, uploaded.json());
    }
  }

  // --- операции ---

  /** Создать папку со всеми промежуточными. Существующая — не ошибка. */
  async ensureDir(path: string): Promise<void> {
    let current = '';
    for (const segment of path.split('/').filter(Boolean)) {
      current = `${current}/${segment}`;
      const r = await this.api('PUT', '/resources', { path: YaDiskClient.diskPath(current) });
      if (r.status !== 201 && r.status !== 409) throw YaDiskClient.fail('PUT', current, r.status, r.json); // 409 — уже есть
    }
  }

  /** Имена в папке. Отсутствующая папка — пустой список. */
  async listDir(path: string): Promise<string[]> {
    const r = await this.api('GET', '/resources', {
      path: YaDiskClient.diskPath(path), limit: '1000', fields: '_embedded.items.name',
    });
    if (r.status === 404) return [];
    if (r.status !== 200) throw YaDiskClient.fail('GET', path, r.status, r.json);
    const embedded = r.json._embedded as { items?: unknown } | undefined;
    const items = Array.isArray(embedded?.items) ? embedded.items as unknown[] : [];
    return items.flatMap((item) => {
      const name = (item as { name?: unknown } | null)?.name;
      return typeof name === 'string' ? [name] : [];
    });
  }

  /**
   * Записать файл (существующий заменяется). Большой файл выгружается под
   * временным именем и переименовывается: так обходится замедление по
   * расширению, а оборванная выгрузка не оставит под настоящим именем недописанный файл.
   */
  async put(path: string, data: Uint8Array, opts: { timeoutMs?: number; onProgress?: Progress } = {}): Promise<void> {
    if (data.length <= DIRECT_UPLOAD_MAX) {
      await this.upload(path, data, opts.timeoutMs, opts.onProgress);
      return;
    }
    const temp = path + TEMP_SUFFIX;
    await this.upload(temp, data, opts.timeoutMs, opts.onProgress);
    const r = await this.api('POST', '/resources/move', {
      from: YaDiskClient.diskPath(temp), path: YaDiskClient.diskPath(path), overwrite: 'true',
    });
    await this.finish('POST', `move to ${path}`, r, opts.timeoutMs);
  }

  /** Содержимое файла; null, если файла нет. */
  async get(
    path: string, opts: { timeoutMs?: number; maxBytes?: number; onProgress?: Progress } = {},
  ): Promise<Uint8Array | null> {
    const link = await this.api('GET', '/resources/download', { path: YaDiskClient.diskPath(path) });
    if (link.status === 404) return null;
    const href = link.status === 200 ? link.json.href : undefined;
    if (typeof href !== 'string') throw YaDiskClient.fail('GET', `download link for ${path}`, link.status, link.json);
    // Ссылка на скачивание подписана и ведёт на другой хост — без токена.
    const downloaded = await httpRequest(this.transport, {
      method: 'GET', url: href, headers: {}, timeoutMs: opts.timeoutMs ?? this.timeoutMs,
      maxBytes: opts.maxBytes ?? DEFAULT_MAX_BYTES, onProgress: opts.onProgress,
    });
    if (downloaded.status === 404) return null;
    if (downloaded.status !== 200) throw YaDiskClient.fail('GET', path, downloaded.status);
    return downloaded.body;
  }

  /**
   * Копия файла на стороне Диска (без передачи по сети). Уже существующий
   * target не заменяется и ошибкой не считается.
   */
  async copy(source: string, target: string, timeoutMs?: number): Promise<void> {
    const r = await this.api('POST', '/resources/copy', {
      from: YaDiskClient.diskPath(source), path: YaDiskClient.diskPath(target), overwrite: 'false',
    });
    if (r.status === 409) return;
    await this.finish('POST', `copy to ${target}`, r, timeoutMs);
  }

  /**
   * Удалить файл в Корзину Диска. Отсутствующий — не ошибка. Фоновое
   * удаление (202) не дожидаемся: на результат синхронизации оно не влияет.
   */
  async delete(path: string): Promise<void> {
    const r = await this.api('DELETE', '/resources', { path: YaDiskClient.diskPath(path) });
    if (![202, 204, 404].includes(r.status)) throw YaDiskClient.fail('DELETE', path, r.status, r.json);
  }
}

interface XhrLike {
  open(method: string, url: string): void;
  setRequestHeader(name: string, value: string): void;
  getResponseHeader(name: string): string | null;
  send(body?: unknown): void;
  abort(): void;
  responseType: string;
  timeout: number;
  readonly status: number;
  readonly response: unknown;
  readonly responseURL: string;
  readonly upload: { onprogress: ((e: { loaded: number }) => void) | null };
  onprogress: ((e: { loaded: number; total: number; lengthComputable: boolean }) => void) | null;
  onload: (() => void) | null;
  onerror: (() => void) | null;
  ontimeout: (() => void) | null;
  onabort: (() => void) | null;
}

/**
 * Transport на XMLHttpRequest React Native: только он сообщает ход передачи.
 * Обрыв ответа сверяется с Content-Length (иначе он выглядел бы как «файл
 * повреждён»); слишком большой ответ прерывается, не дожидаясь конца.
 */
export function xhrTransport(createXhr: () => XhrLike = () => new XMLHttpRequest() as unknown as XhrLike): Transport {
  return {
    request(req) {
      return new Promise<HttpResponse>((resolve, reject) => {
        const xhr = createXhr();
        let tooLarge = false;
        const fail = (why: string) => reject(new YaDiskError(`Network error on ${req.method}: ${why}`));
        xhr.open(req.method, req.url);
        xhr.responseType = 'arraybuffer';
        xhr.timeout = req.timeoutMs;
        for (const [name, value] of Object.entries(req.headers)) xhr.setRequestHeader(name, value);
        const body = req.body;
        const onProgress = req.onProgress;
        if (body && body.length && onProgress) {
          xhr.upload.onprogress = (e) => onProgress(Math.min(e.loaded, body.length), body.length);
        }
        // Слушатель ставится всегда: по нему же прерывается слишком большой ответ.
        xhr.onprogress = (e) => {
          if (e.loaded > req.maxBytes) {
            tooLarge = true;
            xhr.abort();
            return;
          }
          if (onProgress && !body) onProgress(e.loaded, e.lengthComputable ? e.total : 0);
        };
        xhr.onerror = () => fail('connection failed');
        xhr.ontimeout = () => fail('timed out');
        xhr.onabort = () => (tooLarge
          ? reject(new YaDiskError(`${req.method} response is larger than ${req.maxBytes} bytes`))
          : fail('aborted'));
        xhr.onload = () => {
          const raw = xhr.response;
          const data = raw instanceof ArrayBuffer ? new Uint8Array(raw) : new Uint8Array(0);
          const declared = xhr.getResponseHeader('content-length');
          if (declared && /^\d+$/.test(declared) && data.length < Number(declared)) {
            fail('response was cut off');
            return;
          }
          resolve({ status: xhr.status, body: data, finalUrl: xhr.responseURL || req.url });
        };
        xhr.send(body ?? null);
      });
    },
  };
}
