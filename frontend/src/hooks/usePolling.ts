import { useCallback, useEffect, useRef, useState } from 'react';

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
  const [isPolling, setIsPolling] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stoppedRef = useRef(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const poll = useCallback(async () => {
    if (stoppedRef.current) return;
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
      if (shouldStop?.(result)) {
        stoppedRef.current = true;
        setIsPolling(false);
        return;
      }
    } catch (err) {
      setError(err as Error);
    }
    if (!stoppedRef.current) {
      timerRef.current = setTimeout(poll, interval);
    }
  }, [interval, shouldStop]);

  useEffect(() => {
    if (!enabled) return;
    stoppedRef.current = false;
    setIsPolling(true);
    poll();
    return () => {
      stoppedRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      setIsPolling(false);
    };
  }, [enabled, poll]);

  const refresh = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    stoppedRef.current = false;
    setIsPolling(true);
    poll();
  }, [poll]);

  return { data, error, isPolling, refresh };
}
