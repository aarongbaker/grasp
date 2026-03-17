import type { SessionStatus } from '../../types/api';
import styles from './StatusBadge.module.css';

const STATUS_LABELS: Record<SessionStatus, string> = {
  pending: 'Pending',
  generating: 'Generating',
  enriching: 'Enriching',
  validating: 'Validating',
  scheduling: 'Scheduling',
  complete: 'Complete',
  partial: 'Partial',
  failed: 'Failed',
};

export function StatusBadge({ status }: { status: SessionStatus }) {
  return (
    <span className={`${styles.badge} ${styles[status]}`}>
      <span className={styles.dot} />
      {STATUS_LABELS[status]}
    </span>
  );
}
