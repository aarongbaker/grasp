import { useState } from 'react';
import { Link } from 'react-router-dom';
import { XIcon } from 'lucide-react';
import { StatusBadge } from '../shared/StatusBadge';
import { MEAL_TYPE_LABELS, OCCASION_LABELS, type Session } from '../../types/api';
import { getSessionConceptDisplay } from './sessionConceptDisplay';
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

interface SessionCardProps {
  session: Session;
  onDelete?: (sessionId: string) => void;
}

export function SessionCard({ session, onDelete }: SessionCardProps) {
  const { concept_json: c } = session;
  const conceptDisplay = getSessionConceptDisplay(c);
  const [confirming, setConfirming] = useState(false);

  function handleDeleteClick(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (confirming) {
      onDelete?.(session.session_id);
    } else {
      setConfirming(true);
    }
  }

  function handleCancelConfirm(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setConfirming(false);
  }

  return (
    <Link to={`/sessions/${session.session_id}`} className={styles.card}>
      <div className={styles.header}>
        <div className={styles.titleBlock}>
          <div className={styles.sourceLabel}>{conceptDisplay.sourceLabel}</div>
          <div className={styles.concept}>{conceptDisplay.title}</div>
          <div className={styles.sourceDetail}>{conceptDisplay.pathwayLabel} · {conceptDisplay.sourceDetail}</div>
        </div>
        <div className={styles.headerActions}>
          <StatusBadge status={session.status} />
          {onDelete && (
            confirming ? (
              <span className={styles.confirmGroup}>
                <button
                  className={styles.confirmBtn}
                  onClick={handleDeleteClick}
                  aria-label="Confirm delete session"
                >
                  Delete
                </button>
                <button
                  className={styles.cancelBtn}
                  onClick={handleCancelConfirm}
                  aria-label="Cancel delete"
                >
                  Keep
                </button>
              </span>
            ) : (
              <button
                className={styles.deleteBtn}
                onClick={handleDeleteClick}
                aria-label="Delete session"
              >
                <XIcon size={14} />
              </button>
            )
          )}
        </div>
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
