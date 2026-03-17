import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getSessionResults } from '../api/sessions';
import { StatusBadge } from '../components/shared/StatusBadge';
import { Skeleton } from '../components/shared/Skeleton';
import { PipelineProgress } from '../components/session/PipelineProgress';
import { ScheduleTimeline } from '../components/session/ScheduleTimeline';
import { RecipeCard } from '../components/session/RecipeCard';
import { useSessionStatus } from '../hooks/useSessionStatus';
import { TERMINAL_STATUSES, type SessionResults } from '../types/api';
import styles from './SessionDetailPage.module.css';

type Tab = 'schedule' | 'recipes';

export function SessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { data: session, isPolling } = useSessionStatus(sessionId);
  const [results, setResults] = useState<SessionResults | null>(null);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [tab, setTab] = useState<Tab>('schedule');

  const isTerminal = session && TERMINAL_STATUSES.includes(session.status);
  const isFailed = session?.status === 'failed';

  // Fetch full results when session reaches terminal state
  useEffect(() => {
    if (!sessionId || !isTerminal || isFailed) return;
    setResultsLoading(true);
    getSessionResults(sessionId)
      .then(setResults)
      .catch(() => {})
      .finally(() => setResultsLoading(false));
  }, [sessionId, isTerminal, isFailed]);

  if (!session) {
    return (
      <div className={styles.page}>
        <Skeleton variant="heading" />
        <div className={styles.loadingContent}>
          <Skeleton variant="card" count={2} />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <Link to="/" className={styles.backLink}>&#x2190; Back to sessions</Link>

      <div className={styles.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)', marginBottom: 'var(--space-sm)' }}>
          <h1 className={styles.title}>Session</h1>
          <StatusBadge status={session.status} />
        </div>
        <p className={styles.conceptText}>{session.concept_json.free_text}</p>
      </div>

      {/* In-progress state */}
      {!isTerminal && (
        <>
          <PipelineProgress status={session.status} />
          <div className={styles.loadingContent}>
            <Skeleton variant="timeline" count={4} />
            <Skeleton variant="card" count={2} />
          </div>
        </>
      )}

      {/* Failed state */}
      {isFailed && (
        <div className={styles.errorBanner}>
          <div className={styles.errorTitle}>Pipeline failed</div>
          {session.error_summary || 'An unexpected error occurred. Please try again.'}
        </div>
      )}

      {/* Terminal with results */}
      {isTerminal && !isFailed && (
        <>
          {session.status === 'partial' && session.error_summary && (
            <div className={styles.errorBanner}>
              <div className={styles.errorTitle}>Completed with issues</div>
              {session.error_summary}
            </div>
          )}

          {session.schedule_summary && (
            <div className={styles.summary}>{session.schedule_summary}</div>
          )}

          {resultsLoading ? (
            <div className={styles.loadingContent}>
              <Skeleton variant="timeline" count={4} />
              <Skeleton variant="card" count={2} />
            </div>
          ) : results ? (
            <>
              <div className={styles.tabBar}>
                <button
                  className={`${styles.tab} ${tab === 'schedule' ? styles.tabActive : ''}`}
                  onClick={() => setTab('schedule')}
                >
                  Schedule
                </button>
                <button
                  className={`${styles.tab} ${tab === 'recipes' ? styles.tabActive : ''}`}
                  onClick={() => setTab('recipes')}
                >
                  Recipes ({results.recipes.length})
                </button>
              </div>

              {tab === 'schedule' && <ScheduleTimeline schedule={results.schedule} />}
              {tab === 'recipes' && (
                <div className={styles.recipeList}>
                  {results.recipes.map((r) => (
                    <RecipeCard key={r.source.source.name} recipe={r} />
                  ))}
                </div>
              )}
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
