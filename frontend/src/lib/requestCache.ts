export type RequestCache = <T>(key: string, request: () => Promise<T>) => Promise<T>;

export const createRequestCache = (): RequestCache => {
  const pending = new Map<string, Promise<unknown>>();
  return <T>(key: string, request: () => Promise<T>): Promise<T> => {
    const existing = pending.get(key) as Promise<T> | undefined;
    if (existing) return existing;
    const current = request().finally(() => {
      if (pending.get(key) === current) pending.delete(key);
    });
    pending.set(key, current);
    return current;
  };
};
