import { apiFetch } from './client';
import type { BookRecord, IngestionJob } from '../types/api';

export function uploadPdf(file: File): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch<{ job_id: string }>('/ingest', {
    method: 'POST',
    body: form,
  });
}

export function getIngestionStatus(jobId: string): Promise<IngestionJob> {
  return apiFetch<IngestionJob>(`/ingest/${jobId}`);
}

export function listCookbooks(): Promise<BookRecord[]> {
  return apiFetch<BookRecord[]>('/ingest/cookbooks');
}
