import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeftIcon, DownloadIcon } from 'lucide-react';
import { pdf } from '@react-pdf/renderer';
import { cancelSession, getSessionResults } from '../api/sessions';
import { Button } from '../components/shared/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { Skeleton } from '../components/shared/Skeleton';
import { PipelineProgress } from '../components/session/PipelineProgress';
import { ScheduleTimeline } from '../components/session/ScheduleTimeline';
import { RecipeCard } from '../components/session/RecipeCard';
import { RecipePDF } from '../components/session/RecipePDF';
import { getSessionConceptDisplay } from '../components/session/sessionConceptDisplay';
import { useSessionStatus } from '../hooks/useSessionStatus';
import { TERMINAL_STATUSES, type SessionResults } from '../types/api';
import styles from './SessionDetailPage.module.css';

type Tab = 'schedule' | 'recipes';

export function SessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { data: session } = useSessionStatus(sessionId);
  const [results, setResults] = useState<SessionResults | null>(null);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('schedule');
  const [pdfLoading, setPdfLoading] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const isTerminal = session && TERMINAL_STATUSES.includes(session.status);
  const isFailed = session?.status === 'failed';
  const isCancelled = session?.status === 'cancelled';
  const conceptDisplay = session ? getSessionConceptDisplay(session.concept_json) : null;

  async function handleCancel() {
    if (!sessionId || cancelling) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelSession(sessionId);
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : 'Failed to cancel');
    } finally {
      setCancelling(false);
    }
  }

  async function handleDownloadPDF() {
    if (!results || !session) return;
    setPdfLoading(true);
    try {
      const blob = await pdf(<RecipePDF session={session} results={results} />).toBlob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `grasp-session-${sessionId}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setPdfLoading(false);
    }
  }

  // Fetch full results when session reaches terminal state
  useEffect(() => {
    if (!sessionId || !isTerminal || isFailed) return;
    setResultsLoading(true);
    setResultsError(null);
    getSessionResults(sessionId)
      .then(setResults)
      .catch((err) => {
        setResultsError(err instanceof Error ? err.message : 'Failed to load results');
      })
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
      <Link to="/" className={styles.backLink}>
        <ArrowLeftIcon size={14} />
        Back to sessions
      </Link>

      <div className={styles.header}>
        <div className={styles.titleRow}>
          <h1 className={styles.title}>Session</h1>
          <StatusBadge status={session.status} />
        </div>
        <div className={styles.conceptBadgeRow}>
          <span className={styles.conceptLabel}>{conceptDisplay?.sourceLabel ?? 'Generated plan'}</span>
          <span className={styles.conceptMeta}>{conceptDisplay?.pathwayLabel ?? 'Plan a Dinner'}</span>
        </div>
        <p className={styles.conceptText}>{conceptDisplay?.title ?? session.concept_json.free_text}</p>
        <p className={styles.conceptSourceDetail}>{conceptDisplay?.sourceDetail ?? 'Built from the current session concept.'}</p>
      </div>

      {/* In-progress state */}
      {!isTerminal && (
        <>
          <div className={styles.progressRow}>
            <PipelineProgress status={session.status} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
              {cancelError && <span className={styles.cancelError}>{cancelError}</span>}
              <Button variant="secondary" size="sm" onClick={handleCancel} disabled={cancelling}>
                {cancelling ? 'Cancelling...' : 'Cancel'}
              </Button>
            </div>
          </div>
          <div className={styles.loadingContent}>
            <Skeleton variant="timeline" count={4} />
            <Skeleton variant="card" count={2} />
          </div>
        </>
      )}

      {/* Cancelled state */}
      {isCancelled && (
        <div className={styles.errorBanner}>
          <div className={styles.errorTitle}>Session cancelled</div>
          Pipeline was cancelled. No tokens will be used for this session going forward.
        </div>
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

          <PipelineProgress status={session.status} />

          {session.schedule_summary && (
            <div className={styles.summary}>{session.schedule_summary}</div>
          )}

          {resultsLoading ? (
            <div className={styles.loadingContent}>
              <Skeleton variant="timeline" count={4} />
              <Skeleton variant="card" count={2} />
            </div>
          ) : resultsError ? (
            <div className={styles.errorBanner}>
              <div className={styles.errorTitle}>Could not load results</div>
              {resultsError}
              <div style={{ marginTop: 'var(--space-sm)' }}>
                <Button variant="secondary" size="sm" onClick={() => {
                  if (!sessionId) return;
                  setResultsLoading(true);
                  setResultsError(null);
                  getSessionResults(sessionId)
                    .then(setResults)
                    .catch((err) => {
                      setResultsError(err instanceof Error ? err.message : 'Failed to load results');
                    })
                    .finally(() => setResultsLoading(false));
                }}>
                  Try again
                </Button>
              </div>
            </div>
          ) : results ? (
            <>
              <div className={styles.tabRow}>
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
                <Button variant="secondary" size="sm" onClick={handleDownloadPDF} disabled={pdfLoading}>
                  <DownloadIcon size={14} />
                  {pdfLoading ? 'Generating...' : 'Download PDF'}
                </Button>
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
