import { useCallback, useEffect, useMemo, useState } from 'react';
import { uploadPdf, getIngestionStatus, listCookbooks, deleteCookbook } from '../api/ingest';
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
            <span className={`${styles.jobBadge} ${styles[job.status]}`}>{job.status}</span>
          </div>
          <div className={styles.bookList}>
            {job.book_statuses.map((b, i) => (
              <div key={i} className={styles.bookItem}>
                <span>{b.title}</span>
                <span>{b.status}</span>
                {b.error && <span className={styles.bookError}>{b.error}</span>}
              </div>
            ))}
          </div>
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
