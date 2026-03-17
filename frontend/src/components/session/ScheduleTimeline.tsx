import { RESOURCE_LABELS, type NaturalLanguageSchedule, type TimelineEntry } from '../../types/api';
import styles from './ScheduleTimeline.module.css';

function formatDuration(min: number, max: number | null): string {
  if (max && max !== min) return `${min}–${max} min`;
  return `${min} min`;
}

function formatTotalDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

function TimelineRow({ entry }: { entry: TimelineEntry }) {
  return (
    <div className={styles.entry}>
      <div className={styles.time}>{entry.label}</div>
      <div className={styles.content}>
        <div className={styles.recipeName}>{entry.recipe_name}</div>
        <div className={styles.action}>{entry.action}</div>
        {entry.heads_up && <div className={styles.headsUp}>{entry.heads_up}</div>}
      </div>
      <div className={styles.meta}>
        <span className={`${styles.resourceBadge} ${styles[entry.resource]}`}>
          {RESOURCE_LABELS[entry.resource]}
        </span>
        <span className={styles.durationText}>
          {formatDuration(entry.duration_minutes, entry.duration_max)}
        </span>
        {entry.is_prep_ahead && entry.prep_ahead_window && (
          <span className={styles.prepAhead}>{entry.prep_ahead_window}</span>
        )}
      </div>
    </div>
  );
}

export function ScheduleTimeline({ schedule }: { schedule: NaturalLanguageSchedule }) {
  const prepAhead = schedule.timeline.filter((e) => e.is_prep_ahead);
  const mainTimeline = schedule.timeline.filter((e) => !e.is_prep_ahead);

  return (
    <div className={styles.timeline}>
      <div>
        <div className={styles.totalDuration}>{formatTotalDuration(schedule.total_duration_minutes)}</div>
        <div className={styles.totalLabel}>total cook time</div>
      </div>

      {prepAhead.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>Prep Ahead</h3>
          <div className={styles.entries}>
            {prepAhead.map((entry) => (
              <TimelineRow key={entry.step_id} entry={entry} />
            ))}
          </div>
        </div>
      )}

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>Day-Of Timeline</h3>
        <div className={styles.entries}>
          {mainTimeline.map((entry) => (
            <TimelineRow key={entry.step_id} entry={entry} />
          ))}
        </div>
      </div>
    </div>
  );
}
