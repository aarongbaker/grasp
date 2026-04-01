import { apiFetch } from './client';
import type { BookRecord, DetectedRecipeCandidate, IngestionJob } from '../types/api';

export function uploadPdf(file: File): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch<{ job_id: string }>('/ingest', {
    method: 'POST',
    body: form,
  });
}

export function cancelIngestion(jobId: string): Promise<{ job_id: string; status: string; message: string }> {
  return apiFetch<{ job_id: string; status: string; message: string }>(`/ingest/${jobId}/cancel`, {
    method: 'POST',
  });
}

export function getIngestionStatus(jobId: string): Promise<IngestionJob> {
  return apiFetch<IngestionJob>(`/ingest/${jobId}`);
}

export function listCookbooks(): Promise<BookRecord[]> {
  return apiFetch<BookRecord[]>('/ingest/cookbooks');
}

export function listDetectedRecipes(): Promise<DetectedRecipeCandidate[]> {
  return apiFetch<DetectedRecipeCandidate[]>('/ingest/detected-recipes');
}

export function deleteCookbook(bookId: string): Promise<void> {
  return apiFetch<void>(`/ingest/cookbooks/${bookId}`, {
    method: 'DELETE',
  });
}
