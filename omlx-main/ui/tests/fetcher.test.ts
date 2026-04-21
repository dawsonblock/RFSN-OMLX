import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { z } from 'zod';
import { ApiError, request, jsonBody, API_BASE } from '../src/lib/fetcher';

const origFetch = globalThis.fetch;

function mockFetch(status: number, body: unknown, opts: { ok?: boolean; textBody?: boolean } = {}) {
  const ok = opts.ok ?? (status >= 200 && status < 300);
  const jsonImpl = opts.textBody
    ? () => Promise.reject(new Error('not json'))
    : () => Promise.resolve(body);
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: 'StatusText',
    json: jsonImpl,
  }) as unknown as typeof fetch;
}

describe('lib/fetcher', () => {
  afterEach(() => {
    globalThis.fetch = origFetch;
    vi.restoreAllMocks();
  });

  it('API_BASE points at /ui/api', () => {
    expect(API_BASE).toBe('/ui/api');
  });

  it('jsonBody stringifies and sets POST', () => {
    const init = jsonBody({ a: 1 });
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"a":1}');
  });

  it('parses a successful response through the schema', async () => {
    mockFetch(200, { a: 1 });
    const schema = z.object({ a: z.number() });
    const out = await request(schema, '/x');
    expect(out).toEqual({ a: 1 });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/ui/api/x',
      expect.objectContaining({ headers: expect.objectContaining({ 'content-type': 'application/json' }) }),
    );
  });

  it('throws ApiError with backend detail on non-OK JSON body', async () => {
    mockFetch(400, { detail: 'bad request here' });
    await expect(request(z.any(), '/x')).rejects.toMatchObject({
      status: 400,
      detail: 'bad request here',
    });
  });

  it('falls back to statusText when body is not JSON', async () => {
    mockFetch(500, undefined, { textBody: true });
    await expect(request(z.any(), '/x')).rejects.toBeInstanceOf(ApiError);
    try {
      await request(z.any(), '/x');
    } catch (e) {
      expect((e as ApiError).detail).toBe('StatusText');
    }
  });

  it('propagates zod errors when the response shape is wrong', async () => {
    mockFetch(200, { wrong: true });
    const schema = z.object({ a: z.number() });
    await expect(request(schema, '/x')).rejects.toThrow();
  });
});

describe('ApiError', () => {
  beforeEach(() => { /* noop */ });
  it('composes status and detail into message', () => {
    const e = new ApiError(404, 'nope');
    expect(e.message).toBe('[404] nope');
    expect(e).toBeInstanceOf(Error);
  });
});
