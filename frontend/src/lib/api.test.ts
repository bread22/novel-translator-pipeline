import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, request, withAuthQuery } from './api';

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  sessionStorage.removeItem('web_auth_token');
});

describe('API client', () => {
  it('parses the structured error envelope and request ID', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ detail: { code: 'INVALID', detail: 'bad input' } }),
      { status: 422, headers: { 'content-type': 'application/json', 'x-request-id': 'req-7' } },
    ));
    await expect(request('/broken')).rejects.toEqual(expect.objectContaining({
      status: 422, code: 'INVALID', detail: 'bad input', requestId: 'req-7',
    }));
  });

  it('accepts a 204 response without JSON parsing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }));
    await expect(request('/empty')).resolves.toBeUndefined();
  });

  it('combines external cancellation with its request signal', async () => {
    const external = new AbortController();
    vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(init.signal?.reason));
    }));
    const pending = request('/slow', undefined, { signal: external.signal });
    external.abort(new DOMException('cancelled', 'AbortError'));
    await expect(pending).rejects.toMatchObject({ name: 'ApiError', code: 'REQUEST_ABORTED' });
  });

  it('aborts a request when its timeout expires', async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(init.signal?.reason));
    }));
    const pending = request('/slow', undefined, { timeoutMs: 25 });
    const assertion = expect(pending).rejects.toMatchObject({ name: 'ApiError', code: 'REQUEST_TIMEOUT' });
    await vi.advanceTimersByTimeAsync(25);
    await assertion;
  });

  it('adds a bearer token sourced from the browser session to API requests and downloads', async () => {
    sessionStorage.setItem('web_auth_token', 'fixture-token');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));

    await request('/protected');

    const fetchInit = vi.mocked(fetch).mock.calls[0][1];
    expect(new Headers(fetchInit?.headers).get('Authorization')).toBe('Bearer fixture-token');
    expect(withAuthQuery('/api/v1/books/book/download')).toBe(
      `${window.location.origin}/api/v1/books/book/download?access_token=fixture-token`,
    );
  });
});

describe('SSE client', () => {
  it('uses one global stream, reports state, preserves envelopes, and closes', () => {
    class FakeEventSource {
      static instance: FakeEventSource;
      static CLOSED = 2;
      readyState = 1;
      onopen: (() => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      listeners = new Map<string, (event: MessageEvent) => void>();
      close = vi.fn();
      constructor(public url: string) { FakeEventSource.instance = this; }
      addEventListener(name: string, listener: EventListener) { this.listeners.set(name, listener as (event: MessageEvent) => void); }
    }
    vi.stubGlobal('EventSource', FakeEventSource);
    const events = vi.fn();
    const states = vi.fn();
    const close = api.subscribeEvents(events, states);
    const source = FakeEventSource.instance;
    expect(source.url).toBe('/api/v1/events/stream');
    source.onopen?.();
    expect(states).toHaveBeenCalledWith('live');
    source.listeners.get('queue_updated')?.(new MessageEvent('queue_updated', {
      data: JSON.stringify({ event: 'queue_updated', data: { pending_count: 1 }, book_id: null, timestamp: 'now', event_id: 'evt-1' }),
      lastEventId: 'evt-1',
    }));
    expect(events).toHaveBeenCalledWith(expect.objectContaining({ event_id: 'evt-1', book_id: null }));
    source.readyState = FakeEventSource.CLOSED;
    source.onerror?.(new Event('error'));
    expect(states).toHaveBeenCalledWith('offline');
    close();
    expect(source.close).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
