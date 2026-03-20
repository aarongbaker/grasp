import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { deleteSession, listSessions } from '../api/sessions';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/shared/Button';
import { Skeleton } from '../components/shared/Skeleton';
import { SessionCard } from '../components/session/SessionCard';
import type { Session } from '../types/api';
import styles from './DashboardPage.module.css';

export function DashboardPage() {
  const { userId } = useAuth();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSessions = useCallback(() => {
    if (!userId) return;
    setLoading(true);
    setError(null);
    listSessions(userId)
      .then(setSessions)
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load sessions');
      })
      .finally(() => setLoading(false));
  }, [userId]);

  const handleDelete = useCallback(async (sessionId: string) => {
    try {
      await deleteSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete session');
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  return (
    <div>
      <div className={styles.header}>
        <h1 className={styles.title}>Your Sessions</h1>
        <Link to="/sessions/new">
          <Button>Plan a Dinner</Button>
        </Link>
      </div>

      {loading ? (
        <div className={styles.loadingList}>
          <Skeleton variant="card" count={3} />
        </div>
      ) : error ? (
        <div className={styles.errorState}>
          <p className={styles.errorText}>{error}</p>
          <Button variant="secondary" onClick={fetchSessions}>Try again</Button>
        </div>
      ) : sessions.length === 0 ? (
        <div className={styles.empty}>
          <div className={styles.emptyIcon}>&#x1F37D;</div>
          <h2 className={styles.emptyTitle}>No sessions yet</h2>
          <p className={styles.emptyText}>Plan your first dinner party and let grasp handle the scheduling.</p>
          <Link to="/sessions/new">
            <Button>Plan a Dinner</Button>
          </Link>
        </div>
      ) : (
        <div className={styles.sessionList}>
          {sessions.map((s) => (
            <SessionCard key={s.session_id} session={s} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </div>
  );
}
