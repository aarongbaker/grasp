import { PIPELINE_STAGES, type SessionStatus } from '../../types/api';
import styles from './PipelineProgress.module.css';

const STAGE_LABELS: Record<string, string> = {
  generating: 'Generating',
  enriching: 'Enriching',
  validating: 'Validating',
  scheduling: 'Scheduling',
  complete: 'Complete',
};

export function PipelineProgress({ status }: { status: SessionStatus }) {
  const currentIdx = PIPELINE_STAGES.indexOf(status);

  return (
    <div className={styles.progress}>
      {PIPELINE_STAGES.map((stage, i) => {
        const isDone = i < currentIdx;
        const isActive = i === currentIdx;

        return (
          <div key={stage} className={styles.stage}>
            {i > 0 && <div className={`${styles.connector} ${isDone ? styles.done : ''}`} />}
            <div className={`${styles.dot} ${isActive ? styles.active : ''} ${isDone ? styles.done : ''}`} />
            <span
              className={`${styles.stageLabel} ${isActive ? styles.active : ''} ${isDone ? styles.done : ''}`}
            >
              {STAGE_LABELS[stage]}
            </span>
          </div>
        );
      })}
    </div>
  );
}
