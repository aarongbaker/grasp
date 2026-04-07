import { useMemo } from 'react';
import type { TimelineEntry } from '../../types/api';
import { LANE_COLORS } from './colors';
import styles from './CookingGantt.module.css';

interface CookingGanttProps {
  timeline: TimelineEntry[];
  totalDurationMinutes: number;
}

interface LaneConfig {
  recipe: string;
  tasks: TimelineEntry[];
}

interface BarSegment {
  key: string;
  startMin: number;
  endMin: number;
  endWithBuffer: number;
  label: string;
  tooltip: string;
}

const MERGE_GAP_MINUTES = -1;
const PX_PER_MINUTE = 4;

function formatOffset(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `+${m}m`;
  if (m === 0) return `+${h}h`;
  return `+${h}:${String(m).padStart(2, '0')}`;
}

function clockTimeAtOffset(baseClockTime: string, offsetMinutes: number): string {
  let baseDate: Date;
  if (baseClockTime.includes('T')) {
    baseDate = new Date(baseClockTime);
  } else if (baseClockTime.includes('AM') || baseClockTime.includes('PM')) {
    const match = baseClockTime.match(/(\d+):(\d+)\s*(AM|PM)/i);
    if (!match) return formatOffset(offsetMinutes);
    let h = parseInt(match[1], 10);
    const m = parseInt(match[2], 10);
    if (match[3].toUpperCase() === 'PM' && h !== 12) h += 12;
    if (match[3].toUpperCase() === 'AM' && h === 12) h = 0;
    baseDate = new Date(2000, 0, 1, h, m);
  } else {
    const [h, m] = baseClockTime.split(':').map(Number);
    baseDate = new Date(2000, 0, 1, h, m);
  }
  const resultDate = new Date(baseDate.getTime() + offsetMinutes * 60000);
  const hours = resultDate.getHours();
  const minutes = resultDate.getMinutes();
  const ampm = hours >= 12 ? 'PM' : 'AM';
  const h12 = hours % 12 || 12;
  return `${h12}:${String(minutes).padStart(2, '0')} ${ampm}`;
}

function buildSegment(tasks: TimelineEntry[], stepNumberMap: Map<string, number>): BarSegment {
  const first = tasks[0];
  const last = tasks[tasks.length - 1];
  const startMin = first.time_offset_minutes;
  const endMin = last.time_offset_minutes + last.duration_minutes;
  const endWithBuffer = last.time_offset_minutes + last.duration_minutes + (last.buffer_minutes ?? 0);
  const nums = tasks.map((t) => stepNumberMap.get(t.step_id) ?? 0);

  const label = nums.length === 1 ? String(nums[0]) : `${nums[0]}–${nums[nums.length - 1]}`;
  const tooltip = tasks
    .map((t) => {
      const n = stepNumberMap.get(t.step_id) ?? 0;
      return `${n}. ${t.action}`;
    })
    .join('\n');

  return {
    key: tasks.map((t) => t.step_id).join('+'),
    startMin,
    endMin,
    endWithBuffer,
    label,
    tooltip,
  };
}

function mergeTasks(tasks: TimelineEntry[], stepNumberMap: Map<string, number>): BarSegment[] {
  if (tasks.length === 0) return [];

  const sorted = [...tasks].sort((a, b) => a.time_offset_minutes - b.time_offset_minutes);
  const segments: BarSegment[] = [];
  let current: TimelineEntry[] = [sorted[0]];

  for (let i = 1; i < sorted.length; i++) {
    const prev = current[current.length - 1];
    const prevEnd = prev.time_offset_minutes + prev.duration_minutes + (prev.buffer_minutes ?? 0);
    const nextStart = sorted[i].time_offset_minutes;

    if (nextStart - prevEnd <= MERGE_GAP_MINUTES) current.push(sorted[i]);
    else {
      segments.push(buildSegment(current, stepNumberMap));
      current = [sorted[i]];
    }
  }

  segments.push(buildSegment(current, stepNumberMap));
  return segments;
}

export function CookingGantt({ timeline, totalDurationMinutes }: CookingGanttProps) {
  const windowStart = useMemo(() => {
    if (timeline.length === 0) return 0;
    return Math.min(...timeline.map((e) => e.time_offset_minutes));
  }, [timeline]);

  const windowDuration = totalDurationMinutes - windowStart;

  const recipeColorMap = useMemo(() => {
    const seen = new Map<string, string>();
    for (const entry of timeline) {
      if (!seen.has(entry.recipe_name)) {
        // Use neutral gray for merged prep lane
        const isMerged = entry.merged_from && entry.merged_from.length > 0;
        if (isMerged) {
          seen.set(entry.recipe_name, '#7a8080');
        } else {
          seen.set(entry.recipe_name, LANE_COLORS[seen.size % LANE_COLORS.length]);
        }
      }
    }
    return seen;
  }, [timeline]);

  const lanes = useMemo(() => {
    const map = new Map<string, TimelineEntry[]>();
    for (const entry of timeline) {
      const existing = map.get(entry.recipe_name);
      if (existing) existing.push(entry);
      else map.set(entry.recipe_name, [entry]);
    }
    return Array.from(map, ([recipe, tasks]) => ({ recipe, tasks } satisfies LaneConfig));
  }, [timeline]);

  const timeMarkers = useMemo(() => {
    const interval = windowDuration <= 90 ? 15 : windowDuration <= 240 ? 30 : 60;
    const firstEntry = timeline.find((e) => e.clock_time != null);
    const baseClockTime = firstEntry
      ? (() => {
          const ct = firstEntry.clock_time!;
          let baseDate: Date;
          if (ct.includes('T')) {
            baseDate = new Date(ct);
          } else if (ct.includes('AM') || ct.includes('PM')) {
            const match = ct.match(/(\d+):(\d+)\s*(AM|PM)/i);
            if (!match) return null;
            let h = parseInt(match[1], 10);
            const m = parseInt(match[2], 10);
            if (match[3].toUpperCase() === 'PM' && h !== 12) h += 12;
            if (match[3].toUpperCase() === 'AM' && h === 12) h = 0;
            baseDate = new Date(2000, 0, 1, h, m);
          } else {
            const [h, m] = ct.split(':').map(Number);
            baseDate = new Date(2000, 0, 1, h, m);
          }
          const base = new Date(baseDate.getTime() - firstEntry.time_offset_minutes * 60000);
          const hours = base.getHours();
          const minutes = base.getMinutes();
          const ampm = hours >= 12 ? 'PM' : 'AM';
          const h12 = hours % 12 || 12;
          return `${h12}:${String(minutes).padStart(2, '0')} ${ampm}`;
        })()
      : null;

    const markers: Array<{ min: number; label: string }> = [];
    const firstMarker = Math.floor(windowStart / interval) * interval;
    for (let m = firstMarker; m <= totalDurationMinutes; m += interval) {
      markers.push({
        min: m,
        label: baseClockTime ? clockTimeAtOffset(baseClockTime, m) : formatOffset(m),
      });
    }
    return markers;
  }, [timeline, totalDurationMinutes, windowStart, windowDuration]);

  const stepNumbers = useMemo(() => {
    const map = new Map<string, number>();
    const counters = new Map<string, number>();
    for (const entry of [...timeline].sort((a, b) => a.time_offset_minutes - b.time_offset_minutes)) {
      const count = (counters.get(entry.recipe_name) ?? 0) + 1;
      counters.set(entry.recipe_name, count);
      map.set(entry.step_id, count);
    }
    return map;
  }, [timeline]);

  if (lanes.length === 0) return null;

  const scrollMinWidth = windowDuration * PX_PER_MINUTE + 180;

  return (
    <section className={styles.section} aria-label="Cooking overview diagram">
      <h3 className={styles.title}>Steps at a Glance</h3>
      <div className={styles.container}>
        <div className={styles.scrollArea}>
          <div className={styles.scrollContent} style={{ minWidth: `${scrollMinWidth}px` }}>
            <div className={styles.timeAxis}>
              {timeMarkers.map((marker) => (
                <div
                  key={marker.min}
                  className={styles.timeLabel}
                  style={{ left: `${((marker.min - windowStart) / windowDuration) * 100}%` }}
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
                    style={{ left: `${((marker.min - windowStart) / windowDuration) * 100}%` }}
                  />
                ))}
                <div className={styles.serveLine} style={{ left: '100%' }} />
              </div>

              <div className={styles.lanes}>
                {lanes.map((lane) => {
                  const color = recipeColorMap.get(lane.recipe) ?? LANE_COLORS[0];
                  const segments = mergeTasks(lane.tasks, stepNumbers);
                  const isMerged = lane.tasks[0]?.merged_from && lane.tasks[0].merged_from.length > 0;
                  const laneLabel = isMerged ? 'Shared Prep' : lane.recipe;

                  return (
                    <div key={lane.recipe} className={styles.lane}>
                      <div className={styles.laneLabel}>{laneLabel}</div>
                      <div className={styles.barArea}>
                        {segments.map((seg) => {
                          const leftPct = ((seg.startMin - windowStart) / windowDuration) * 100;
                          const solidDur = seg.endMin - seg.startMin;
                          const bufferDur = seg.endWithBuffer - seg.endMin;
                          const rawSolidPct = (solidDur / windowDuration) * 100;
                          const rawBufferPct = (bufferDur / windowDuration) * 100;
                          const maxWidth = Math.max(0, 100 - leftPct);
                          const totalPct = Math.min(rawSolidPct + rawBufferPct, maxWidth);
                          const scale = totalPct / (rawSolidPct + rawBufferPct || 1);
                          const solidPct = rawSolidPct * scale;
                          const bufferPct = rawBufferPct * scale;
                          const solidWidthPct = (solidPct / (solidPct + bufferPct || 1)) * 100;
                          const bufferWidthPct = (bufferPct / (solidPct + bufferPct || 1)) * 100;

                          // Check for oven temp and preheat status on tasks in this segment
                          const segmentTasks = lane.tasks.filter((t) =>
                            seg.key.split('+').includes(t.step_id)
                          );
                          const ovenTemp = segmentTasks.find(
                            (t) => t.resource === 'oven' && t.oven_temp_f != null
                          )?.oven_temp_f;
                          const isPreheat = segmentTasks.some((t) => t.is_preheat === true);

                          return (
                            <div
                              key={seg.key}
                              className={styles.barGroup}
                              style={{ left: `${leftPct}%`, width: `${solidPct + bufferPct}%` }}
                              title={seg.tooltip}
                              tabIndex={0}
                            >
                              <div
                                className={`${styles.bar} ${isPreheat ? styles.preheatBar : ''}`}
                                style={{ width: `${solidWidthPct}%`, backgroundColor: color }}
                              >
                                <span className={styles.barLabel}>
                                  {seg.label}
                                  {ovenTemp != null && ` · ${ovenTemp}°F`}
                                </span>
                              </div>
                              {bufferPct > 0 && (
                                <div
                                  className={styles.bufferBar}
                                  style={{ width: `${bufferWidthPct}%`, backgroundColor: color }}
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
        </div>
      </div>
    </section>
  );
}
