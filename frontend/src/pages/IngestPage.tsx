import { useState } from 'react';
import { uploadPdf, getIngestionStatus } from '../api/ingest';
import { FileUpload } from '../components/shared/FileUpload';
import { Button } from '../components/shared/Button';
import { usePolling } from '../hooks/usePolling';
import type { IngestionJob } from '../types/api';
import styles from './IngestPage.module.css';

export function IngestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');

  const { data: job } = usePolling<IngestionJob>({
    fetcher: () => getIngestionStatus(jobId!),
    interval: 3000,
    shouldStop: (j) => j.status === 'complete' || j.status === 'failed',
    enabled: !!jobId,
  });

  async function handleUpload() {
    if (!file) return;
    setError('');
    setUploading(true);
    try {
      const res = await uploadPdf(file);
      setJobId(res.job_id);
    } catch (err: any) {
      setError(err.detail || 'Upload failed');
    } finally {
      setUploading(false);
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
    </div>
  );
}
