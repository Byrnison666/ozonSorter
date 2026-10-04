/**
 * Transport для тестов на fetch Bun (XMLHttpRequest в Bun нет). Ход скачивания
 * сообщается по мере чтения тела, ход выгрузки — одним отчётом в конце: fetch
 * не сообщает, сколько отправлено.
 */
import { type HttpResponse, type Transport, YaDiskError } from '../../src/core/sync/yadisk';

export const fetchTransport: Transport = {
  async request(req): Promise<HttpResponse> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), req.timeoutMs);
    const fail = (why: string) => new YaDiskError(`Network error on ${req.method}: ${why}`);
    try {
      let response: Response;
      try {
        response = await fetch(req.url, {
          // Типы React Native не включают Uint8Array в BodyInit.
          method: req.method, headers: req.headers, body: req.body as unknown as BodyInit | undefined,
          signal: controller.signal,
        });
      } catch (e) {
        throw fail((e as Error).message);
      }
      if (req.body?.length) req.onProgress?.(req.body.length, req.body.length);
      const declared = response.headers.get('content-length');
      const total = declared && /^\d+$/.test(declared) ? Number(declared) : 0;
      if (total > req.maxBytes) throw new YaDiskError(`${req.method} response is larger than ${req.maxBytes} bytes`);
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        const reader = response.body?.getReader();
        while (reader) {
          const { done, value: chunk } = await reader.read();
          if (done) break;
          chunks.push(chunk);
          size += chunk.length;
          if (size > req.maxBytes) {
            controller.abort();
            throw new YaDiskError(`${req.method} response is larger than ${req.maxBytes} bytes`);
          }
          if (!req.body) req.onProgress?.(size, total);
        }
      } catch (e) {
        if (e instanceof YaDiskError) throw e;
        throw fail((e as Error).message);
      }
      if (total && size < total) throw fail('response was cut off');
      const body = new Uint8Array(size);
      let offset = 0;
      for (const c of chunks) {
        body.set(c, offset);
        offset += c.length;
      }
      return { status: response.status, body, finalUrl: response.url || req.url };
    } finally {
      clearTimeout(timer);
    }
  },
};
