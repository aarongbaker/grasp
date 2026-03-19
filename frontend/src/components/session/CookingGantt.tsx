import { useMemo } from 'react';
import type { TimelineEntry } from '../../types/api';
import styles from './CookingGantt.module.css';

interface CookingGanttProps {
  timeline: TimelineEntry[];
  totalDurationMinutes: number;
}

interface LaneConfig {
  recipe: string;
  tasks: TimelineEntry[];
}

const LANE_COLORS = [
  '#c9813a',
  '#7aad7a',
  '#c46b6b',
  '#6a8fa3',
  '#d4a24e',
  '#9b7bb8',
  '#d4956a',
  '#5c8a6a',
];

function formatOffset(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `+${m}m`;
  if (m === 0) return `+${h}h`;
  return `+${h}:${String(m).padStart(2, '0')}`;
}

/** Pull the first verb/keyword from the action text */
function oneWord(action: string): string {
  const skip = new Set([
    'the', 'a', 'an', 'of', 'in', 'to', 'and', 'with', 'for', 'on', 'at',
    'into', 'from', 'until', 'while', 'then',
  ]);
  const words = action.split(/[\s.,:;]+/).filter((w) => w.length > 1);
  const word = words.find((w) => !skip.has(w.toLowerCase()));
  return word ?? words[0] ?? action.slice(0, 6);
}

export function CookingGantt({ timeline, totalDurationMinutes }: CookingGanttProps) {
  // Defensive filter: exclude any prep-ahead entries that slip through
  const dayOfTimeline = useMemo(
    () => timeline.filter((e) => !e.is_prep_ahead),
    [timeline],
  );

  // Build a stable recipe→color mapping
  const recipeColorMap = useMemo(() => {
    const seen = new Map<string, string>();
    for (const entry of dayOfTimeline) {
      if (!seen.has(entry.recipe_name)) {
        seen.set(entry.recipe_name, LANE_COLORS[seen.size % LANE_COLORS.length]);
      }
    }
    return seen;
  }, [dayOfTimeline]);

  const lanes = useMemo(() => {
    const map = new Map<string, TimelineEntry[]>();
    for (const entry of dayOfTimeline) {
      const existing = map.get(entry.recipe_name);
      if (existing) existing.push(entry);
      else map.set(entry.recipe_name, [entry]);
    }
    const result: LaneConfig[] = [];
    for (const [recipe, tasks] of map) {
      result.push({
        recipe,
        tasks: tasks.sort((a, b) => a.time_offset_minutes - b.time_offset_minutes),
      });
    }
    return result;
  }, [dayOfTimeline]);

  const timeMarkers = useMemo(() => {
    const markers: Array<{ min: number; label: string }> = [];
    for (let m = 0; m <= totalDurationMinutes; m += 30) {
      markers.push({ min: m, label: formatOffset(m) });
    }
    return markers;
  }, [totalDurationMinutes]);

  if (lanes.length === 0) return null;

  return (
    <section className={styles.section} aria-label="Cooking overview diagram">
      <h3 className={styles.title}>At a Glance</h3>

      <div className={styles.container}>
        <div className={styles.timeAxis}>
          {timeMarkers.map((marker) => (
            <div
              key={marker.min}
              className={styles.timeLabel}
              style={{ left: `${(marker.min / totalDurationMinutes) * 100}%` }}
            >
              {marker.label}
            </div>
          ))}
          <div className={styles.serveLabel} style={{ left: '100%' }}>
            Serve
          </div>
        </div>

        <div className={styles.chart}>
          <div className={styles.gridLines}>
            {timeMarkers.map((marker) => (
              <div
                key={marker.min}
                className={styles.gridLine}
                style={{ left: `${(marker.min / totalDurationMinutes) * 100}%` }}
              />
            ))}
            <div className={styles.serveLine} style={{ left: '100%' }} />
          </div>

          <div className={styles.lanes}>
            {lanes.map((lane) => {
              const color = recipeColorMap.get(lane.recipe) ?? LANE_COLORS[0];

              return (
                <div key={lane.recipe} className={styles.lane}>
                  <div className={styles.laneLabel}>{lane.recipe}</div>
                  <div className={styles.barArea}>
                    {lane.tasks.map((task) => {
                      const leftPct = (task.time_offset_minutes / totalDurationMinutes) * 100;
                      const rawDurPct = (task.duration_minutes / totalDurationMinutes) * 100;
                      const rawBufferPct = task.buffer_minutes
                        ? (task.buffer_minutes / totalDurationMinutes) * 100
                        : 0;
                      // Clamp so bars never extend past the serve line
                      const maxWidth = Math.max(0, 100 - leftPct);
                      const totalPct = Math.min(rawDurPct + rawBufferPct, maxWidth);
                      const scale = totalPct / (rawDurPct + rawBufferPct || 1);
                      const durPct = rawDurPct * scale;
                      const bufferPct = rawBufferPct * scale;

                      return (
                        <div
                          key={task.step_id}
                          className={styles.barGroup}
                          style={{ left: `${leftPct}%`, width: `${durPct + bufferPct}%` }}
                          title={task.action}
                          tabIndex={0}
                        >
                          <div
                            className={styles.bar}
                            style={{ width: `${(durPct / (durPct + bufferPct)) * 100}%`, backgroundColor: color }}
                          >
                            <span className={styles.barLabel}>{oneWord(task.action)}</span>
                          </div>
                          {bufferPct > 0 && (
                            <div
                              className={styles.bufferBar}
                              style={{
                                width: `${(bufferPct / (durPct + bufferPct)) * 100}%`,
                                backgroundColor: color,
                              }}
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
