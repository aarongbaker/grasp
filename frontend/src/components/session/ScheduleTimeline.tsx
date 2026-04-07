import { AlertTriangleIcon, ClockIcon } from 'lucide-react';
import { type NaturalLanguageSchedule, type TimelineEntry } from '../../types/api';
import { CookingGantt } from './CookingGantt';
import { LANE_COLORS } from './colors';
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

function HeadsUpCallout({ text }: { text: string }) {
  return (
    <div className={styles.headsUp}>
      <AlertTriangleIcon size={14} className={styles.headsUpIcon} />
      <span className={styles.headsUpText}>{text}</span>
    </div>
  );
}

function TimelineRow({ entry, isLast, stepNum, stepColor }: { entry: TimelineEntry; isLast: boolean; stepNum?: number; stepColor?: string }) {
  const isMerged = entry.merged_from && entry.merged_from.length > 0;
  const isPreheat = entry.is_preheat === true;
  const hasOvenTemp = entry.resource === 'oven' && entry.oven_temp_f != null;
  
  return (
    <div className={styles.timelineRow}>
      {/* Time label */}
      <div className={styles.timeLabel}>{entry.label}</div>

      {/* Content with colored left border */}
      <div
        className={`${styles.rowContent} ${isLast ? styles.rowContentLast : ''} ${isPreheat ? styles.preheatStep : ''}`}
        style={stepColor ? { borderLeftColor: stepColor } : undefined}
      >
        <div className={styles.recipeName}>
          {stepNum != null && <span className={styles.stepNum} style={stepColor ? { color: stepColor } : undefined}>{stepNum}.</span>}
          {isMerged ? (
            <>
              <span className={styles.sharedPrepLabel}>Shared Prep</span>
              <span className={styles.sharedPrepBadge}>SHARED PREP</span>
            </>
          ) : isPreheat ? (
            <>
              <span className={styles.preheatLabel}>Preheat</span>
              <span className={styles.preheatBadge}>PREHEAT</span>
            </>
          ) : (
            entry.recipe_name
          )}
        </div>
        <p className={styles.action}>{entry.action}</p>
        <div className={styles.inlineMeta}>
          <span className={styles.durationText}>
            <ClockIcon size={12} />
            {formatDuration(entry.duration_minutes, entry.duration_max)}
          </span>
          {entry.prep_ahead_window && (
            <span className={styles.prepAheadTag} title="This step can be done ahead of time">
              up to {entry.prep_ahead_window}
            </span>
          )}
          {hasOvenTemp && (
            <span className={styles.ovenTempBadge} title="Oven temperature">
              {entry.oven_temp_f}°F
            </span>
          )}
        </div>
        {entry.heads_up && <HeadsUpCallout text={entry.heads_up} />}
      </div>
    </div>
  );
}

export function ScheduleTimeline({ schedule }: { schedule: NaturalLanguageSchedule }) {
  // Combine timeline with any legacy prep_ahead_entries (backwards compat with old session data)
  const allEntries = (() => {
    const legacyPrepAhead = schedule.prep_ahead_entries ?? [];
    if (legacyPrepAhead.length > 0) {
      // Old session data: timeline contains day-of only, prep_ahead_entries are separate
      const merged = [...schedule.timeline, ...legacyPrepAhead];
      return merged.sort((a, b) => a.time_offset_minutes - b.time_offset_minutes);
    }
    // New data: timeline already contains everything
    return schedule.timeline;
  })();

  return (
    <div className={styles.timeline}>
      {/* Total cook time */}
      <div>
        <div className={styles.totalDuration}>
          {formatTotalDuration(schedule.total_duration_minutes)}
          {schedule.total_duration_minutes_max != null && (
            <span className={styles.worstCase}> – {formatTotalDuration(schedule.total_duration_minutes_max)}</span>
          )}
        </div>
        <div className={styles.totalLabel}>
          total cook time
          {schedule.active_time_minutes != null && (
            <span className={styles.activeTime}> · {formatTotalDuration(schedule.active_time_minutes)} active</span>
          )}
        </div>
      </div>

      {/* Cooking Gantt chart — receives the full timeline */}
      <CookingGantt timeline={allEntries} totalDurationMinutes={schedule.total_duration_minutes} />

      {/* Recipe Steps — single unified section */}
      <section aria-label="Recipe steps">
        <h3 className={styles.sectionTitle}>Recipe Steps</h3>
        <div>
          {(() => {
            // Build recipe→color map matching the Gantt chart order
            const colorMap = new Map<string, string>();
            for (const entry of allEntries) {
              if (!colorMap.has(entry.recipe_name)) {
                colorMap.set(entry.recipe_name, LANE_COLORS[colorMap.size % LANE_COLORS.length]);
              }
            }
            const counters = new Map<string, number>();
            return allEntries.map((entry, i) => {
              const count = (counters.get(entry.recipe_name) ?? 0) + 1;
              counters.set(entry.recipe_name, count);
              return (
                <TimelineRow
                  key={entry.step_id}
                  entry={entry}
                  isLast={i === allEntries.length - 1}
                  stepNum={count}
                  stepColor={colorMap.get(entry.recipe_name)}
                />
              );
            });
          })()}
        </div>
      </section>
    </div>
  );
}
