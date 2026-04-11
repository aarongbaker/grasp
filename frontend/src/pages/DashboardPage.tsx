import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { deleteSession, listSessions } from '../api/sessions';
import { useAuth } from '../context/useAuth';
import { Button } from '../components/shared/Button';
import { Skeleton } from '../components/shared/Skeleton';
import { SessionCard } from '../components/session/SessionCard';
import { PATHWAYS } from '../components/layout/pathways';
import type { Session } from '../types/api';
import styles from './DashboardPage.module.css';

export function DashboardPage() {
  const { userId } = useAuth();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSessions = useCallback(async () => {
    if (!userId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await listSessions(userId);
      setSessions(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sessions');
    } finally {
      setLoading(false);
    }
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
    void fetchSessions();
  }, [fetchSessions]);

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <p className={styles.kicker}>Chef dashboard</p>
          <h1 className={styles.title}>Your Sessions</h1>
        </div>
        <Link to="/sessions/new">
          <Button>Plan a Dinner</Button>
        </Link>
      </div>

      <section className={styles.creationRail} aria-labelledby="creation-rail-title">
        <div className={styles.creationRailHeader}>
          <h2 id="creation-rail-title" className={styles.creationRailTitle}>
            Begin from the right workspace
          </h2>
          <p className={styles.creationRailText}>
            Keep dinner planning, private library browsing, catalog discovery, and chef-authored drafting separate so each flow speaks the language of the work.
          </p>
        </div>

        <div className={styles.creationGrid}>
          {PATHWAYS.map((path) => (
            <article key={path.to} className={styles.creationCard}>
              <div>
                <h3 className={styles.creationCardTitle}>{path.title}</h3>
                <p className={styles.creationCardDescription}>{path.purpose}</p>
                <p className={styles.creationCardRelationship}>{path.relationship}</p>
              </div>
              <Link to={path.to} className={styles.creationCardLink}>
                {path.cta}
              </Link>
            </article>
          ))}
        </div>
      </section>

      {loading ? (
        <div className={styles.loadingList}>
          <Skeleton variant="card" count={3} />
        </div>
      ) : error ? (
        <div className={styles.errorState}>
          <p className={styles.errorText}>{error}</p>
          <Button variant="secondary" onClick={() => void fetchSessions()}>Try again</Button>
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
