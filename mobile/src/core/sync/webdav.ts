/**
 * WebDAV-клиент для синхронизации (Яндекс.Диск) — перенос src/webdav.py.
 * Сетевой обмен вынесен в Transport: на телефоне — fetch React Native,
 * в тестах — fetch Bun. React Native не даёт DOMParser и полноценного URL,
 * поэтому адреса и ответ PROPFIND разбираются вручную.
 */
import { strToU8 } from 'fflate';

export const DEFAULT_MAX_BYTES = 8 * 1024 * 1024;
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '[::1]']);

export class WebDavError extends Error {
  constructor(
    message: string,
    /** Код HTTP; null — сбой соединения. */
    readonly status: number | null = null,
    readonly isTlsError = false,
  ) {
    super(message);
  }

  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403;
  }

  /** Нет связи. Ошибку сертификата повтор «когда появится интернет» не лечит. */
  get isNetworkError(): boolean {
    return this.status === null && !this.isTlsError;
  }
}

export interface HttpRequest {
  method: string;
  url: string;
  headers: Record<string, string>;
  body?: Uint8Array;
  timeoutMs: number;
  maxBytes: number;
}

export interface HttpResponse {
  status: number;
  body: Uint8Array;
  /** Адрес после переадресаций. */
  finalUrl: string;
}

/** Сетевой обмен. Сбой соединения — WebDavError со status null. */
export interface Transport {
  request(req: HttpRequest): Promise<HttpResponse>;
}

function splitUrl(url: string): { scheme: string; host: string; path: string } {
  const m = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/([^/?#]*)([^?#]*)/.exec(url);
  if (!m) return { scheme: '', host: '', path: url.split(/[?#]/)[0] };
  const authority = m[2].replace(/^[^@]*@/, '');
  const host = authority.startsWith('[')
    ? authority.slice(0, authority.indexOf(']') + 1)
    : authority.replace(/:\d*$/, '');
  return { scheme: m[1].toLowerCase(), host: host.toLowerCase(), path: m[3] };
}

/** https либо http на локальный адрес (тестовый сервер). */
export function isSecureUrl(url: string): boolean {
  const { scheme, host } = splitUrl(url);
  return scheme === 'https' || (scheme === 'http' && LOOPBACK_HOSTS.has(host));
}

/** urllib.parse.quote(s, safe=""). */
function pyQuote(s: string): string {
  return encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

function pyUnquote(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s; // как и unquote, битые последовательности оставляем как есть
  }
}

function xmlUnescape(s: string): string {
  return s.replace(/&(#x[0-9a-fA-F]+|#\d+|amp|lt|gt|quot|apos);/g, (_, e: string) => {
    if (e[0] === '#') {
      return String.fromCodePoint(e[1] === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
    }
    return { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[e]!;
  });
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

function base64(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const [a, b, c] = [bytes[i], bytes[i + 1], bytes[i + 2]];
    out += B64[a >> 2] + B64[((a & 3) << 4) | ((b ?? 0) >> 4)];
    out += b === undefined ? '=' : B64[((b & 15) << 2) | ((c ?? 0) >> 6)];
    out += c === undefined ? '=' : B64[c & 63];
  }
  return out;
}

export class WebDavClient {
  readonly baseUrl: string;
  private readonly auth: string;

  constructor(
    baseUrl: string, login: string, password: string,
    private readonly transport: Transport,
    readonly timeoutMs = 30_000,
  ) {
    // Логин и пароль уходят в каждом запросе (Basic) — без TLS их видно в сети.
    if (!isSecureUrl(baseUrl)) throw new Error('WebDAV URL must use https');
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.auth = `Basic ${base64(strToU8(`${login}:${password}`))}`;
  }

  private url(path: string): string {
    return `${this.baseUrl}/${path.split('/').filter(Boolean).map(pyQuote).join('/')}`;
  }

  private async request(
    method: string, path: string,
    opts: { body?: Uint8Array; headers?: Record<string, string>; timeoutMs?: number; maxBytes?: number } = {},
  ): Promise<{ status: number; body: Uint8Array }> {
    const maxBytes = opts.maxBytes ?? DEFAULT_MAX_BYTES;
    const response = await this.transport.request({
      method,
      url: this.url(path),
      headers: { Authorization: this.auth, ...opts.headers },
      body: opts.body,
      timeoutMs: opts.timeoutMs ?? this.timeoutMs,
      maxBytes,
    });
    // Переадресация на незащищённый адрес: данным оттуда не доверяем.
    if (!isSecureUrl(response.finalUrl)) {
      throw new WebDavError(`${method}: redirect to insecure URL refused`, 0);
    }
    if (response.body.length > maxBytes) {
      throw new WebDavError(`${method} response is larger than ${maxBytes} bytes`);
    }
    return response;
  }

  private fail(method: string, path: string, status: number): WebDavError {
    return new WebDavError(`${method} ${path} failed with HTTP ${status}`, status);
  }

  /** Создать папку со всеми промежуточными. Существующая — не ошибка. */
  async ensureDir(path: string): Promise<void> {
    let current = '';
    for (const segment of path.split('/').filter(Boolean)) {
      current = `${current}/${segment}`;
      const { status } = await this.request('MKCOL', current);
      if (status !== 201 && status !== 405) throw this.fail('MKCOL', current, status); // 405 — уже есть
    }
  }

  /** Имена в папке (без самой папки). Отсутствующая папка — пустой список. */
  async listDir(path: string): Promise<string[]> {
    const { status, body } = await this.request('PROPFIND', path, { headers: { Depth: '1' } });
    if (status === 404) return [];
    if (status !== 207) throw this.fail('PROPFIND', path, status);
    const own = pyUnquote(splitUrl(this.url(path)).path).replace(/\/+$/, '');
    const text = new TextDecoder().decode(body);
    if (!/<(?:[\w.-]+:)?multistatus[\s>]/.test(text)) {
      throw new WebDavError('Malformed PROPFIND response', status);
    }
    const names: string[] = [];
    for (const m of text.matchAll(/<((?:[\w.-]+:)?)href(?:\s[^>]*)?>([^<]*)<\/\1href\s*>/g)) {
      const entry = pyUnquote(splitUrl(xmlUnescape(m[2]).trim()).path).replace(/\/+$/, '');
      if (entry && entry !== own) names.push(entry.slice(entry.lastIndexOf('/') + 1));
    }
    return names;
  }

  async put(path: string, data: Uint8Array, timeoutMs?: number): Promise<void> {
    const { status } = await this.request('PUT', path, {
      body: data, timeoutMs, headers: { 'Content-Type': 'application/octet-stream' },
    });
    if (status !== 200 && status !== 201 && status !== 204) throw this.fail('PUT', path, status);
  }

  /** Содержимое файла; null, если файла нет. */
  async get(path: string, opts: { timeoutMs?: number; maxBytes?: number } = {}): Promise<Uint8Array | null> {
    const { status, body } = await this.request('GET', path, opts);
    if (status === 404) return null;
    if (status !== 200) throw this.fail('GET', path, status);
    return body;
  }

  /** Удалить файл. Отсутствующий — не ошибка (202 — Яндекс удаляет в фоне). */
  async delete(path: string): Promise<void> {
    const { status } = await this.request('DELETE', path);
    if (![200, 202, 204, 404].includes(status)) throw this.fail('DELETE', path, status);
  }
}

/**
 * Transport на fetch: таймаут через AbortController, обрыв ответа сверяется
 * с Content-Length (иначе он выглядел бы как «файл повреждён»).
 */
export const fetchTransport: Transport = {
  async request(req) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), req.timeoutMs);
    try {
      let response: Response;
      try {
        response = await fetch(req.url, {
          method: req.method,
          headers: req.headers,
          // Типы React Native не включают Uint8Array в BodyInit; поддержку
          // бинарного тела на устройстве проверяет сквозной тест на телефоне.
          body: req.body as unknown as BodyInit | undefined,
          signal: controller.signal,
        });
      } catch (e) {
        throw new WebDavError(`Network error on ${req.method}: ${(e as Error).message}`);
      }
      const declared = response.headers.get('content-length');
      if (declared && /^\d+$/.test(declared) && Number(declared) > req.maxBytes) {
        throw new WebDavError(`${req.method} response is larger than ${req.maxBytes} bytes`);
      }
      let body: Uint8Array;
      try {
        body = new Uint8Array(await response.arrayBuffer());
      } catch (e) {
        throw new WebDavError(`Network error on ${req.method}: ${(e as Error).message}`);
      }
      if (declared && /^\d+$/.test(declared) && body.length < Number(declared)) {
        throw new WebDavError(`Network error on ${req.method}: response was cut off`);
      }
      return { status: response.status, body, finalUrl: response.url || req.url };
    } finally {
      clearTimeout(timer);
    }
  },
};
