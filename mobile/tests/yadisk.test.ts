/**
 * Клиент REST API Диска и транспорт XMLHttpRequest телефона — перенос
 * tests/test_yadisk_api.py ПК плюс то, чего на ПК нет (XHR, ход передачи).
 */
import { describe, expect, test } from 'bun:test';

import {
  type HttpRequest, type HttpResponse, httpRequest, isSecureUrl, RETRY_DELAYS_MS, type Transport, xhrTransport,
  YaDiskClient, YaDiskError,
} from '../src/core/sync/yadisk';

type XhrEvent = { loaded: number; total: number; lengthComputable: boolean };

/** Поддельный XMLHttpRequest: сценарий ответа задаёт тест. */
class FakeXhr {
  static script: (xhr: FakeXhr) => void = () => undefined;
  method = '';
  url = '';
  headers: Record<string, string> = {};
  sent: unknown = undefined;
  responseType = '';
  timeout = 0;
  status = 0;
  response: unknown = null;
  responseURL = '';
  responseHeaders: Record<string, string> = {};
  aborted = false;
  upload: { onprogress: ((e: { loaded: number }) => void) | null } = { onprogress: null };
  onprogress: ((e: XhrEvent) => void) | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  getResponseHeader(name: string): string | null {
    return this.responseHeaders[name.toLowerCase()] ?? null;
  }
  send(body?: unknown) {
    this.sent = body;
    FakeXhr.script(this);
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
  /** Ответ целиком: ход скачивания кусками по chunk байт, затем onload. */
  respond(status: number, body: Uint8Array, { chunk = body.length || 1, length = body.length } = {}) {
    this.status = status;
    this.responseHeaders['content-length'] = String(length);
    for (let next = chunk; ; next += chunk) {
      const loaded = Math.min(next, body.length);
      this.onprogress?.({ loaded, total: length, lengthComputable: true });
      if (this.aborted) return;
      if (loaded >= body.length) break;
    }
    this.response = body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength);
    this.responseURL = this.url;
    this.onload?.();
  }
}

function xhr(script: (x: FakeXhr) => void): { transport: Transport; last: () => FakeXhr } {
  let created: FakeXhr | null = null;
  FakeXhr.script = script;
  const transport = xhrTransport(() => {
    created = new FakeXhr();
    return created;
  });
  return { transport, last: () => created! };
}

const req = (over: Partial<HttpRequest> = {}): HttpRequest => ({
  method: 'GET', url: 'https://downloader.disk.yandex.ru/x', headers: {}, timeoutMs: 1000, maxBytes: 1000, ...over,
});

describe('транспорт XMLHttpRequest', () => {
  test('ход скачивания: переданные байты и размер', async () => {
    const data = new Uint8Array(300).fill(7);
    const { transport, last } = xhr((x) => x.respond(200, data, { chunk: 100 }));
    const seen: Array<[number, number]> = [];
    const r = await transport.request(req({ onProgress: (d, t) => seen.push([d, t]) }));
    expect(r.status).toBe(200);
    expect(r.body).toEqual(data);
    expect(seen).toEqual([[100, 300], [200, 300], [300, 300]]);
    expect(last().responseType).toBe('arraybuffer');
    expect(last().timeout).toBe(1000);
  });

  test('ход выгрузки — по телу запроса, не по ответу', async () => {
    const body = new Uint8Array(500);
    const { transport, last } = xhr((x) => {
      x.upload.onprogress?.({ loaded: 200 });
      x.upload.onprogress?.({ loaded: 600 }); // с заголовками может прийти больше тела
      x.respond(201, new Uint8Array(0));
    });
    const seen: Array<[number, number]> = [];
    await transport.request(req({ method: 'PUT', body, onProgress: (d, t) => seen.push([d, t]) }));
    expect(seen).toEqual([[200, 500], [500, 500]]);
    expect(last().sent).toBe(body);
  });

  test('заголовки передаются', async () => {
    const { transport, last } = xhr((x) => x.respond(200, new Uint8Array(0)));
    await transport.request(req({ headers: { Authorization: 'OAuth t' } }));
    expect(last().headers).toEqual({ Authorization: 'OAuth t' });
  });

  test('ответ больше предела прерывается, не дожидаясь конца', async () => {
    const { transport, last } = xhr((x) => x.respond(200, new Uint8Array(5000), { chunk: 600 }));
    const err = await transport.request(req({ maxBytes: 1000 })).catch((e) => e);
    expect(err).toBeInstanceOf(YaDiskError);
    expect(err.message).toContain('larger than 1000');
    expect(last().aborted).toBe(true);
  });

  test('обрыв ответа — сетевая ошибка, а не повреждённый файл', async () => {
    const { transport } = xhr((x) => x.respond(200, new Uint8Array(100), { length: 150 }));
    const err = await transport.request(req()).catch((e) => e);
    expect(err).toBeInstanceOf(YaDiskError);
    expect(err.isNetworkError).toBe(true);
  });

  test('сбой соединения и таймаут — сетевые ошибки', async () => {
    for (const fire of [(x: FakeXhr) => x.onerror?.(), (x: FakeXhr) => x.ontimeout?.()]) {
      const { transport } = xhr(fire);
      const err = await transport.request(req()).catch((e) => e);
      expect(err).toBeInstanceOf(YaDiskError);
      expect(err.isNetworkError).toBe(true);
    }
  });
});

/** Транспорт по таблице ответов: url без query → функция ответа. */
function routes(table: Record<string, (r: HttpRequest) => Partial<HttpResponse> & { json?: unknown }>) {
  const log: HttpRequest[] = [];
  const transport: Transport = {
    async request(r) {
      log.push(r);
      const key = `${r.method} ${r.url.split('?')[0]}`;
      const handler = table[key];
      if (!handler) throw new Error(`unexpected ${key}`);
      const { json, ...rest } = handler(r);
      const body = json === undefined ? rest.body ?? new Uint8Array(0) : new TextEncoder().encode(JSON.stringify(json));
      return { status: 200, finalUrl: r.url, ...rest, body };
    },
  };
  return { transport, log };
}

const API = 'https://cloud-api.yandex.net/v1/disk';

describe('клиент Диска', () => {
  test('незащищённый адрес API не принимается', () => {
    expect(() => new YaDiskClient('t', 'http://cloud-api.yandex.net/v1/disk', routes({}).transport)).toThrow('https');
    expect(() => new YaDiskClient('t', 'http://127.0.0.1:8080/v1/disk', routes({}).transport)).not.toThrow();
  });

  test('isSecureUrl: только https или локальный http', () => {
    expect(isSecureUrl('https://a.ru/x')).toBe(true);
    expect(isSecureUrl('http://localhost:1/x')).toBe(true);
    expect(isSecureUrl('http://[::1]:1/x')).toBe(true);
    expect(isSecureUrl('http://127.0.0.1.evil.ru/x')).toBe(false);
    expect(isSecureUrl('http://user@127.0.0.1.evil.ru/x')).toBe(false);
    expect(isSecureUrl('ftp://a.ru')).toBe(false);
  });

  test('переадресация на незащищённый адрес — отказ', async () => {
    const transport: Transport = {
      request: async () => ({ status: 200, body: new Uint8Array(0), finalUrl: 'http://evil.ru/x' }),
    };
    await expect(httpRequest(transport, req())).rejects.toThrow('insecure');
  });

  test('токен уходит только в API, ссылки выгрузки и скачивания — без него', async () => {
    const { transport, log } = routes({
      [`GET ${API}/resources/upload`]: () => ({ json: { href: 'https://uploader.disk.yandex.net/u', method: 'PUT' } }),
      'PUT https://uploader.disk.yandex.net/u': () => ({ status: 201 }),
      [`GET ${API}/resources/download`]: () => ({ json: { href: 'https://downloader.disk.yandex.ru/d' } }),
      'GET https://downloader.disk.yandex.ru/d': () => ({ body: new Uint8Array([1, 2]) }),
    });
    const client = new YaDiskClient('secret-token', API, transport);
    await client.put('a/meta.json', new Uint8Array([1]));
    expect(await client.get('a/meta.json')).toEqual(new Uint8Array([1, 2]));
    for (const r of log) {
      expect(Boolean(r.headers.Authorization)).toBe(r.url.startsWith(API));
    }
    expect(log[0].url).toContain(`path=${encodeURIComponent('app:/a/meta.json')}`);
  });

  test('адрес операции с чужого хоста не опрашивается с токеном', async () => {
    const { transport, log } = routes({
      [`POST ${API}/resources/copy`]: () => ({ status: 202, json: { href: 'https://evil.ru/operations/1' } }),
    });
    const client = new YaDiskClient('secret-token', API, transport);
    await expect(client.copy('a', 'b')).rejects.toThrow('unexpected API host');
    expect(log.length).toBe(1);
  });

  test('копия: уже существующая — не ошибка; фоновая — дожидаемся', async () => {
    let polls = 0;
    const { transport } = routes({
      [`POST ${API}/resources/copy`]: (r) => (r.url.includes('exists')
        ? { status: 409, json: { error: 'DiskResourceAlreadyExistsError' } }
        : { status: 202, json: { href: `${API}/operations/7` } }),
      [`GET ${API}/operations/7`]: () => ({ json: { status: ++polls < 2 ? 'in-progress' : 'success' } }),
    });
    const client = new YaDiskClient('t', API, transport);
    await client.copy('a', 'exists');
    await client.copy('a', 'b');
    expect(polls).toBe(2);
  });

  test('неудачная фоновая операция — ошибка', async () => {
    const { transport } = routes({
      [`POST ${API}/resources/move`]: () => ({ status: 202, json: { href: `${API}/operations/8` } }),
      [`GET ${API}/resources/upload`]: () => ({ json: { href: 'https://uploader.disk.yandex.net/u' } }),
      'PUT https://uploader.disk.yandex.net/u': () => ({ status: 201 }),
      [`GET ${API}/operations/8`]: () => ({ json: { status: 'failed' } }),
    });
    const client = new YaDiskClient('t', API, transport);
    const err = await client.put('x.db.gz', new Uint8Array(100_000)).catch((e) => e);
    expect(err.message).toContain('failed');
    expect(err.isUnavailable).toBe(true); // сбой на стороне Диска — пройдёт сам
  });

  test('большой файл — под временным именем, затем перенос с заменой', async () => {
    const { transport, log } = routes({
      [`GET ${API}/resources/upload`]: () => ({ json: { href: 'https://uploader.disk.yandex.net/u' } }),
      'PUT https://uploader.disk.yandex.net/u': () => ({ status: 201 }),
      [`POST ${API}/resources/move`]: () => ({ status: 201 }),
    });
    const client = new YaDiskClient('t', API, transport);
    await client.put('dir/x.db.gz', new Uint8Array(100_000));
    const params = (r: HttpRequest) => new URL(r.url).searchParams;
    expect(params(log[0]).get('path')).toBe('app:/dir/x.db.gz.tmp');
    expect(params(log[2]).get('from')).toBe('app:/dir/x.db.gz.tmp');
    expect(params(log[2]).get('path')).toBe('app:/dir/x.db.gz');
    expect(params(log[2]).get('overwrite')).toBe('true');
  });

  test('отозванный токен — ошибка авторизации', async () => {
    const { transport } = routes({ [`GET ${API}/resources`]: () => ({ status: 401, json: { error: 'UnauthorizedError' } }) });
    const err = await new YaDiskClient('t', API, transport).listDir('a').catch((e) => e);
    expect(err).toBeInstanceOf(YaDiskError);
    expect(err.isAuthError).toBe(true);
  });

  test('список папки: отсутствующая — пусто, мусор в ответе пропускается', async () => {
    let missing = true;
    const { transport } = routes({
      [`GET ${API}/resources`]: () => (missing
        ? { status: 404, json: {} }
        : { json: { _embedded: { items: [{ name: 'a' }, { name: 5 }, null, { name: 'b' }] } } }),
    });
    const client = new YaDiskClient('t', API, transport);
    expect(await client.listDir('x')).toEqual([]);
    missing = false;
    expect(await client.listDir('x')).toEqual(['a', 'b']);
  });

  test('временная недоступность: нет связи, 5xx кроме 507, 429', () => {
    for (const status of [500, 502, 503, 504, 429]) {
      expect(new YaDiskError('x', status).isUnavailable).toBe(true);
      expect(new YaDiskError('x', status).isNetworkError).toBe(false);
    }
    expect(new YaDiskError('x').isUnavailable).toBe(true);
    for (const status of [0, 400, 401, 403, 404, 409, 507]) {
      expect(new YaDiskError('x', status).isUnavailable).toBe(false);
    }
    expect(new YaDiskError('x', null, true).isUnavailable).toBe(false); // TLS сам не пройдёт
  });

  test('непонятный ответ: успешный статус, но не то, что ждали', async () => {
    for (const status of [200, 201, 202]) expect(new YaDiskError('x', status).isBadResponse).toBe(true);
    for (const status of [null, 0, 404, 500]) expect(new YaDiskError('x', status).isBadResponse).toBe(false);
    const page = new TextEncoder().encode('<html>maintenance</html>');
    const { transport } = routes({ [`GET ${API}/resources/download`]: () => ({ status: 200, body: page }) });
    const err = await new YaDiskClient('t', API, transport, 5_000, [0, 0]).get('a.db').catch((e) => e);
    expect(err).toBeInstanceOf(YaDiskError);
    expect(err.isBadResponse).toBe(true);
    expect(err.isUnavailable).toBe(false);
  });

  test('чтение повторяется после временного сбоя, не больше двух раз', async () => {
    let failures = 1;
    const { transport, log } = routes({
      [`GET ${API}/resources`]: () => (failures-- > 0
        ? { status: 503, json: { error: 'ServiceUnavailable' } }
        : { json: { _embedded: { items: [{ name: 'a' }] } } }),
    });
    const client = new YaDiskClient('t', API, transport, 1000, [0, 0]);
    expect(await client.listDir('x')).toEqual(['a']);
    expect(log.length).toBe(2);
    failures = 10;
    log.length = 0;
    const err = await client.listDir('x').catch((e) => e);
    expect(err.isUnavailable).toBe(true);
    expect(log.length).toBe(3);
    expect(RETRY_DELAYS_MS).toEqual([1000, 2000]);
  });

  test('запись и долгие ошибки не повторяются', async () => {
    const { transport, log } = routes({
      [`PUT ${API}/resources`]: () => ({ status: 500, json: {} }),
      [`DELETE ${API}/resources`]: () => ({ status: 503, json: {} }),
      [`GET ${API}/resources`]: () => ({ status: 507, json: {} }),
    });
    const client = new YaDiskClient('t', API, transport, 1000, [0, 0]);
    expect((await client.ensureDir('a').catch((e) => e)).isUnavailable).toBe(true);
    expect((await client.delete('a').catch((e) => e)).isUnavailable).toBe(true);
    expect((await client.listDir('a').catch((e) => e)).isUnavailable).toBe(false);
    expect(log.map((r) => r.method)).toEqual(['PUT', 'DELETE', 'GET']);
  });

  test('удаление: отсутствующий файл — не ошибка', async () => {
    const { transport } = routes({ [`DELETE ${API}/resources`]: () => ({ status: 404, json: {} }) });
    await new YaDiskClient('t', API, transport).delete('x');
  });
});
