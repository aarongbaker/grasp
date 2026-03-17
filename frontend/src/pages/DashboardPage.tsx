import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listSessions } from '../api/sessions';
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

  useEffect(() => {
    if (!userId) return;
    listSessions(userId)
      .then(setSessions)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [userId]);

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
            <SessionCard key={s.session_id} session={s} />
          ))}
        </div>
      )}
    </div>
  );
}
