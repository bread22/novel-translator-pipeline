import { describe, expect, it, vi } from 'vitest';
import { createRequestCache } from './requestCache';

describe('request cache', () => {
  it('coalesces overlapping refreshes and expires after settlement', async () => {
    const cache = createRequestCache();
    const request = vi.fn(async () => 7);
    const [first, second] = await Promise.all([cache('books', request), cache('books', request)]);
    expect([first, second]).toEqual([7, 7]);
    expect(request).toHaveBeenCalledOnce();
    await cache('books', request);
    expect(request).toHaveBeenCalledTimes(2);
  });
});
