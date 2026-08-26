import { describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';

describe('QA regression: export timeout budget', () => {
  it('keeps a long-running export alive beyond the default request timeout', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('timed out', 'AbortError')));
    }))); 

    const pending = api.exportBook('book-1');
    let settled = false;
    void pending.then(() => { settled = true; }, () => { settled = true; });

    await vi.advanceTimersByTimeAsync(30_001);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(90_000);
    await expect(pending).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT' });
    vi.useRealTimers();
  });
});
