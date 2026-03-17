import { useMemo } from 'react';
import { getSession } from '../api/sessions';
import { TERMINAL_STATUSES, type Session } from '../types/api';
import { usePolling } from './usePolling';

export function useSessionStatus(sessionId: string | undefined) {
  const fetcher = useMemo(
    () => (sessionId ? () => getSession(sessionId) : () => Promise.reject(new Error('No session ID'))),
    [sessionId],
  );

  return usePolling<Session>({
    fetcher,
    interval: 2000,
    shouldStop: (s) => TERMINAL_STATUSES.includes(s.status),
    enabled: !!sessionId,
  });
}
