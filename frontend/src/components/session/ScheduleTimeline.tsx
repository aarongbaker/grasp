import { AlertTriangleIcon, ClockIcon, InfoIcon, SparklesIcon } from 'lucide-react';
import { type NaturalLanguageSchedule, type TimelineEntry, type OneOvenConflictSummary } from '../../types/api';
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

function formatRecipeList(recipeNames: string[]): string | null {
  const unique = Array.from(new Set(recipeNames.filter(Boolean)));
  if (unique.length === 0) return null;
  if (unique.length === 1) return unique[0];
  if (unique.length === 2) return `${unique[0]} and ${unique[1]}`;
  return `${unique.slice(0, -1).join(', ')}, and ${unique.at(-1)}`;
}

function normalizeOneOvenConflict(conflict?: OneOvenConflictSummary): Required<Pick<OneOvenConflictSummary, 'classification' | 'tolerance_f' | 'has_second_oven' | 'temperature_gap_f' | 'blocking_recipe_names' | 'affected_step_ids'>> & {
  remediation: {
    requires_resequencing: boolean;
    suggested_actions: string[];
    delaying_recipe_names: string[];
    blocking_recipe_names: string[];
    notes: string | null;
  };
} {
  return {
    classification: conflict?.classification ?? 'compatible',
    tolerance_f: conflict?.tolerance_f ?? 15,
    has_second_oven: conflict?.has_second_oven ?? false,
    temperature_gap_f: conflict?.temperature_gap_f ?? null,
    blocking_recipe_names: conflict?.blocking_recipe_names ?? [],
    affected_step_ids: conflict?.affected_step_ids ?? [],
    remediation: {
      requires_resequencing: conflict?.remediation?.requires_resequencing ?? false,
      suggested_actions: conflict?.remediation?.suggested_actions ?? [],
      delaying_recipe_names: conflict?.remediation?.delaying_recipe_names ?? [],
      blocking_recipe_names: conflict?.remediation?.blocking_recipe_names ?? [],
      notes: conflict?.remediation?.notes ?? null,
    },
  };
}

function HeadsUpCallout({ text }: { text: string }) {
  return (
    <div className={styles.headsUp}>
      <AlertTriangleIcon size={14} className={styles.headsUpIcon} />
      <span className={styles.headsUpText}>{text}</span>
    </div>
  );
}

function OneOvenGuidance({ schedule }: { schedule: NaturalLanguageSchedule }) {
  const conflict = normalizeOneOvenConflict(schedule.one_oven_conflict);

  if (conflict.classification !== 'resequence_required') {
    return null;
  }

  const delayingRecipes = formatRecipeList(conflict.remediation.delaying_recipe_names);
  const blockingRecipes = formatRecipeList(
    conflict.remediation.blocking_recipe_names.length > 0
      ? conflict.remediation.blocking_recipe_names
      : conflict.blocking_recipe_names,
  );

  return (
    <section className={styles.guidanceCard} aria-label="One-oven guidance">
      <div className={styles.guidanceHeader}>
        <div className={styles.guidanceIconWrap}>
          <SparklesIcon size={16} className={styles.guidanceIcon} />
        </div>
        <div>
          <h3 className={styles.guidanceTitle}>One-oven plan still works</h3>
          <p className={styles.guidanceSubtitle}>
            The scheduler already found a workable sequence — stage the oven work instead of trying to bake everything at once.
          </p>
        </div>
      </div>

      <div className={styles.guidanceMeta}>
        {conflict.temperature_gap_f != null && (
          <span className={styles.guidanceBadge}>Temperature gap: {conflict.temperature_gap_f}°F</span>
        )}
        <span className={styles.guidanceBadge}>One-oven tolerance: ±{conflict.tolerance_f}°F</span>
      </div>

      {(blockingRecipes || delayingRecipes) && (
        <div className={styles.guidanceGrid}>
          {blockingRecipes && (
            <div className={styles.guidanceFact}>
              <span className={styles.guidanceFactLabel}>Bake first</span>
              <span className={styles.guidanceFactValue}>{blockingRecipes}</span>
            </div>
          )}
          {delayingRecipes && (
            <div className={styles.guidanceFact}>
              <span className={styles.guidanceFactLabel}>Stage later</span>
              <span className={styles.guidanceFactValue}>{delayingRecipes}</span>
            </div>
          )}
        </div>
      )}

      {conflict.remediation.suggested_actions.length > 0 && (
        <div>
          <div className={styles.guidanceListLabel}>What to do</div>
          <ul className={styles.guidanceList}>
            {conflict.remediation.suggested_actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </div>
      )}

      {conflict.remediation.notes && (
        <div className={styles.guidanceNotes}>
          <InfoIcon size={14} className={styles.guidanceNotesIcon} />
          <span>{conflict.remediation.notes}</span>
        </div>
      )}
    </section>
  );
}

function TimelineRow({ entry, isLast, stepNum, stepColor }: { entry: TimelineEntry; isLast: boolean; stepNum?: number; stepColor?: string }) {
  const isMerged = entry.merged_from && entry.merged_from.length > 0;
  const isPreheat = entry.is_preheat === true;
  const hasOvenTemp = entry.resource === 'oven' && entry.oven_temp_f != null;

  return (
    <div className={styles.timelineRow}>
      <div className={styles.timeLabel}>{entry.label}</div>

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
  const allEntries = (() => {
    const legacyPrepAhead = schedule.prep_ahead_entries ?? [];
    if (legacyPrepAhead.length > 0) {
      const merged = [...schedule.timeline, ...legacyPrepAhead];
      return merged.sort((a, b) => a.time_offset_minutes - b.time_offset_minutes);
    }
    return schedule.timeline;
  })();

  return (
    <div className={styles.timeline}>
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

      <OneOvenGuidance schedule={schedule} />

      <CookingGantt timeline={allEntries} totalDurationMinutes={schedule.total_duration_minutes} />

      <section aria-label="Recipe steps">
        <h3 className={styles.sectionTitle}>Recipe Steps</h3>
        <div>
          {(() => {
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
