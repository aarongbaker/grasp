import { useCallback, useEffect, useMemo, useState } from 'react';

interface UsePollingOptions<T> {
  fetcher: () => Promise<T>;
  interval?: number;
  shouldStop?: (data: T) => boolean;
  enabled?: boolean;
}

interface UsePollingResult<T> {
  data: T | null;
  error: Error | null;
  isPolling: boolean;
  refresh: () => void;
}

export function usePolling<T>({
  fetcher,
  interval = 2000,
  shouldStop,
  enabled = true,
}: UsePollingOptions<T>): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const isPolling = useMemo(() => {
    if (!enabled) return false;
    if (data && shouldStop?.(data)) return false;
    return true;
  }, [data, enabled, shouldStop]);

  const refresh = useCallback(() => {
    if (!enabled) return;
    setTick((value) => value + 1);
  }, [enabled]);

  useEffect(() => {
    if (enabled) return;
    setData(null);
    setError(null);
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const run = async () => {
      try {
        const result = await fetcher();
        if (cancelled) return;

        setData(result);
        setError(null);

        if (shouldStop?.(result)) {
          return;
        }
      } catch (err) {
        if (cancelled) return;
        setError(err as Error);
      }

      timeoutId = setTimeout(() => {
        if (!cancelled) setTick((value) => value + 1);
      }, interval);
    };

    void run();

    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [enabled, fetcher, interval, shouldStop, tick]);

  return { data, error, isPolling, refresh };
}
