import { Link } from 'react-router-dom';
import { StatusBadge } from '../shared/StatusBadge';
import { MEAL_TYPE_LABELS, OCCASION_LABELS, type Session } from '../../types/api';
import styles from './SessionCard.module.css';

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export function SessionCard({ session }: { session: Session }) {
  const { concept_json: c } = session;

  return (
    <Link to={`/sessions/${session.session_id}`} className={styles.card}>
      <div className={styles.header}>
        <div className={styles.concept}>{c.free_text}</div>
        <StatusBadge status={session.status} />
      </div>
      <div className={styles.meta}>
        <span className={styles.pill}>{MEAL_TYPE_LABELS[c.meal_type]}</span>
        <span className={styles.pill}>{OCCASION_LABELS[c.occasion]}</span>
        <span className={styles.pill}>{c.guest_count} guests</span>
        {session.total_duration_minutes && (
          <span className={styles.duration}>{formatDuration(session.total_duration_minutes)}</span>
        )}
      </div>
      {session.schedule_summary && <p className={styles.summary}>{session.schedule_summary}</p>}
      <div className={styles.timestamp}>{formatDate(session.created_at)}</div>
    </Link>
  );
}
