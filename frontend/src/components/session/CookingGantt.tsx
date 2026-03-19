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

/** A visual bar that may represent one or more merged tasks */
interface BarSegment {
  key: string;
  startMin: number;
  endMin: number;       // end of solid portion (no buffer)
  endWithBuffer: number; // end including buffer
  label: string;
  tooltip: string;
  tasks: TimelineEntry[];
  stepNums: number[];
}

export const LANE_COLORS = [
  '#c9813a',
  '#7aad7a',
  '#c46b6b',
  '#6a8fa3',
  '#d4a24e',
  '#9b7bb8',
  '#d4956a',
  '#5c8a6a',
];

const MERGE_GAP_MINUTES = 5; // merge tasks within this gap into activity blocks

function formatOffset(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `+${m}m`;
  if (m === 0) return `+${h}h`;
  return `+${h}:${String(m).padStart(2, '0')}`;
}


/** Merge adjacent small tasks into combined visual bars */
function mergeTasks(tasks: TimelineEntry[], stepNumberMap: Map<string, number>): BarSegment[] {
  if (tasks.length === 0) return [];

  const sorted = [...tasks].sort((a, b) => a.time_offset_minutes - b.time_offset_minutes);
  const segments: BarSegment[] = [];
  let current: TimelineEntry[] = [sorted[0]];

  for (let i = 1; i < sorted.length; i++) {
    const prev = current[current.length - 1];
    const prevEnd = prev.time_offset_minutes + prev.duration_minutes + (prev.buffer_minutes ?? 0);
    const nextStart = sorted[i].time_offset_minutes;

    if (nextStart - prevEnd <= MERGE_GAP_MINUTES) {
      current.push(sorted[i]);
    } else {
      segments.push(buildSegment(current, stepNumberMap));
      current = [sorted[i]];
    }
  }
  segments.push(buildSegment(current, stepNumberMap));
  return segments;
}

function buildSegment(tasks: TimelineEntry[], stepNumberMap: Map<string, number>): BarSegment {
  const first = tasks[0];
  const last = tasks[tasks.length - 1];
  const startMin = first.time_offset_minutes;
  const endMin = last.time_offset_minutes + last.duration_minutes;
  const endWithBuffer = last.time_offset_minutes + last.duration_minutes + (last.buffer_minutes ?? 0);

  const nums = tasks.map((t) => stepNumberMap.get(t.step_id) ?? 0);

  // Label: "1" for single, "1–3" for consecutive range
  let label: string;
  if (nums.length === 1) {
    label = String(nums[0]);
  } else {
    label = `${nums[0]}–${nums[nums.length - 1]}`;
  }

  const tooltip = tasks.map((t) => {
    const n = stepNumberMap.get(t.step_id) ?? 0;
    return `${n}. ${t.action}`;
  }).join('\n');

  return {
    key: tasks.map((t) => t.step_id).join('+'),
    startMin,
    endMin,
    endWithBuffer,
    label,
    tooltip,
    tasks,
    stepNums: nums,
  };
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
      result.push({ recipe, tasks });
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

  // Step numbering: assign a 1-based index per recipe
  const stepNumbers = useMemo(() => {
    const map = new Map<string, number>();
    const counters = new Map<string, number>();
    for (const entry of dayOfTimeline.slice().sort((a, b) => a.time_offset_minutes - b.time_offset_minutes)) {
      const count = (counters.get(entry.recipe_name) ?? 0) + 1;
      counters.set(entry.recipe_name, count);
      map.set(entry.step_id, count);
    }
    return map;
  }, [dayOfTimeline]);

  if (lanes.length === 0) return null;

  return (
    <section className={styles.section} aria-label="Cooking overview diagram">
      <h3 className={styles.title}>Steps at a Glance</h3>

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
              const segments = mergeTasks(lane.tasks, stepNumbers);

              return (
                <div key={lane.recipe} className={styles.lane}>
                  <div className={styles.laneLabel}>{lane.recipe}</div>
                  <div className={styles.barArea}>
                    {segments.map((seg) => {
                      const leftPct = (seg.startMin / totalDurationMinutes) * 100;
                      const solidDur = seg.endMin - seg.startMin;
                      const bufferDur = seg.endWithBuffer - seg.endMin;
                      const rawSolidPct = (solidDur / totalDurationMinutes) * 100;
                      const rawBufferPct = (bufferDur / totalDurationMinutes) * 100;
                      // Clamp so bars never extend past the serve line
                      const maxWidth = Math.max(0, 100 - leftPct);
                      const totalPct = Math.min(rawSolidPct + rawBufferPct, maxWidth);
                      const scale = totalPct / (rawSolidPct + rawBufferPct || 1);
                      const solidPct = rawSolidPct * scale;
                      const bufferPct = rawBufferPct * scale;

                      return (
                        <div
                          key={seg.key}
                          className={styles.barGroup}
                          style={{ left: `${leftPct}%`, width: `${solidPct + bufferPct}%` }}
                          title={seg.tooltip}
                          tabIndex={0}
                        >
                          <div
                            className={styles.bar}
                            style={{ width: '100%', backgroundColor: color }}
                          >
                            <span className={styles.barLabel}>{seg.label}</span>
                          </div>
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
