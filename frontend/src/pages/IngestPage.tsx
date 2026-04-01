import { useCallback, useEffect, useState } from 'react';
import { uploadPdf, cancelIngestion, getIngestionStatus, listCookbooks, deleteCookbook } from '../api/ingest';
import { FileUpload } from '../components/shared/FileUpload';
import { Button } from '../components/shared/Button';
import { usePolling } from '../hooks/usePolling';
import type { BookRecord, IngestionJob } from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './IngestPage.module.css';

const ACTIVE_INGEST_JOB_KEY = 'grasp_active_ingest_job_id';

function readStoredIngestJobId(): string | null {
  if (typeof window === 'undefined') return null;
  const value = window.localStorage.getItem(ACTIVE_INGEST_JOB_KEY)?.trim();
  return value || null;
}

function writeStoredIngestJobId(jobId: string | null) {
  if (typeof window === 'undefined') return;
  if (jobId) {
    window.localStorage.setItem(ACTIVE_INGEST_JOB_KEY, jobId);
    return;
  }
  window.localStorage.removeItem(ACTIVE_INGEST_JOB_KEY);
}

export function IngestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(() => readStoredIngestJobId());
  const [uploading, setUploading] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState('');
  const [cookbooks, setCookbooks] = useState<BookRecord[]>([]);

  const fetchCookbooks = useCallback(async () => {
    try {
      const books = await listCookbooks();
      setCookbooks(books);
      setError('');
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Could not load your cookbook library'));
    }
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void fetchCookbooks();
    }, 0);
    return () => window.clearTimeout(id);
  }, [fetchCookbooks]);

  useEffect(() => {
    writeStoredIngestJobId(jobId);
  }, [jobId]);

  const pollIngestionStatus = useCallback(() => getIngestionStatus(jobId!), [jobId]);

  const shouldStopPolling = useCallback(
    (j: IngestionJob) => {
      if (j.status === 'complete' || j.status === 'failed') {
        if (j.status === 'complete') {
          setFile(null);
          void fetchCookbooks();
        }
        setJobId(null);
        return true;
      }
      return false;
    },
    [fetchCookbooks],
  );

  const { data: job } = usePolling<IngestionJob>({
    fetcher: pollIngestionStatus,
    interval: 3000,
    shouldStop: shouldStopPolling,
    enabled: !!jobId,
  });

  const firstBookStatus = job?.book_statuses[0];
  const updatedAtMs = firstBookStatus?.updated_at ? Date.parse(firstBookStatus.updated_at) : Number.NaN;
  const isStaleProcessing =
    job?.status === 'processing' && Number.isFinite(updatedAtMs) && Date.now() - updatedAtMs > 2 * 60 * 1000;

  async function handleUpload() {
    if (!file) return;
    setError('');
    setUploading(true);
    try {
      const res = await uploadPdf(file);
      setJobId(res.job_id);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(bookId: string) {
    setError('');
    try {
      await deleteCookbook(bookId);
      setCookbooks((prev) => prev.filter((book) => book.book_id !== bookId));
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Could not delete cookbook'));
    }
  }

  async function handleCancelUpload() {
    if (!jobId) return;
    setError('');
    setCancelling(true);
    try {
      await cancelIngestion(jobId);
      setJobId(null);
      setFile(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Could not cancel upload'));
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div>
      <h1 className={styles.title}>Cookbooks</h1>
      <p className={styles.subtitle}>
        Upload your cookbooks to enrich recipe generation with your personal library.
      </p>

      <div className={styles.uploadSection}>
        <FileUpload onFile={setFile} disabled={uploading} />
        {file && !jobId && (
          <div style={{ marginTop: 'var(--space-md)' }}>
            <Button onClick={handleUpload} disabled={uploading}>
              {uploading ? 'Uploading...' : 'Upload & Process'}
            </Button>
          </div>
        )}
        {error && (
          <p style={{ color: 'var(--cost-negative)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-sm)' }}>
            {error}
          </p>
        )}
      </div>

      {job && (
        <div className={styles.jobStatus}>
          <div className={styles.jobHeader}>
            <span className={styles.jobTitle}>Ingestion Job</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
              <span className={`${styles.jobBadge} ${styles[job.status]}`}>{job.status}</span>
              {job.status === 'processing' && (
                <Button variant="ghost" size="sm" onClick={handleCancelUpload} disabled={cancelling}>
                  {cancelling ? 'Cancelling…' : 'Cancel upload'}
                </Button>
              )}
            </div>
          </div>
          <div className={styles.bookList}>
            {job.book_statuses.map((b, i) => (
              <div key={i} className={styles.bookItem}>
                <span>{b.title}</span>
                <span>{b.phase ? `${b.status} · ${b.phase}` : b.status}</span>
                {typeof b.pages_total === 'number' && <span>{b.pages_total} pages</span>}
                {typeof b.chunks_total === 'number' && <span>{b.chunks_total} chunks</span>}
                {typeof b.embedded_chunks === 'number' && <span>{b.embedded_chunks} embedded</span>}
                {b.error && <span className={styles.bookError}>{b.error}</span>}
              </div>
            ))}
          </div>
          {job.status === 'processing' && (
            <>
              <p style={{ fontSize: 'var(--text-sm)', marginTop: 'var(--space-md)' }}>
                {job.book_statuses[0]?.phase === 'ocr'
                  ? 'Scanning pages now. Large cookbooks can take several minutes.'
                  : job.book_statuses[0]?.phase === 'embed'
                    ? 'Building search vectors now. External API latency can slow this stage.'
                    : 'Processing your cookbook.'}
              </p>
              {isStaleProcessing && (
                <p style={{ color: 'var(--cost-warning)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-sm)' }}>
                  Progress has not updated for a couple of minutes. The upload may be stalled.
                  You can keep this page open to see whether it resumes or fails.
                </p>
              )}
            </>
          )}
          {job.status === 'complete' && (
            <p style={{ color: 'var(--cost-positive)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-md)' }}>
              Done! {job.completed} book(s) processed successfully.
            </p>
          )}
        </div>
      )}

      <div className={styles.library}>
        <h2 className={styles.libraryTitle}>Your Library</h2>
        {cookbooks.length === 0 ? (
          <p className={styles.emptyLibrary}>
            Your library is empty — upload a cookbook to get started.
          </p>
        ) : (
          <div className={styles.libraryList}>
            {cookbooks.map((book) => (
              <div key={book.book_id} className={styles.libraryItem}>
                <div className={styles.libraryItemHeader}>
                  <span className={styles.libraryItemTitle}>{book.title}</span>
                  <div className={styles.libraryItemActions}>
                    {book.document_type && (
                      <span className={styles.libraryItemType}>{book.document_type}</span>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(book.book_id)}
                      aria-label={`Delete ${book.title}`}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
                <div className={styles.libraryItemMeta}>
                  {book.author && <span>{book.author}</span>}
                  <span>{book.total_pages} pages</span>
                  <span>{book.total_chunks} chunks</span>
                  <span>{new Date(book.created_at).toLocaleDateString()}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
