import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IngestPage } from '../IngestPage';
import * as ingestApi from '../../api/ingest';
import type { BookRecord, IngestionJob } from '../../types/api';

const ACTIVE_INGEST_JOB_KEY = 'grasp_active_ingest_job_id';
const storage = new Map<string, string>();

const localStorageMock = {
  getItem: vi.fn((key: string) => storage.get(key) ?? null),
  setItem: vi.fn((key: string, value: string) => {
    storage.set(key, value);
  }),
  removeItem: vi.fn((key: string) => {
    storage.delete(key);
  }),
  clear: vi.fn(() => {
    storage.clear();
  }),
};

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
});

const library: BookRecord[] = [
  {
    book_id: 'book-1',
    title: 'Sunday Suppers',
    author: 'Test Author',
    document_type: 'cookbook',
    total_pages: 120,
    total_chunks: 8,
    created_at: '2026-03-31T00:00:00Z',
  },
];

function buildJob(status: IngestionJob['status']): IngestionJob {
  return {
    job_id: 'job-123',
    user_id: 'user-1',
    status,
    book_count: 1,
    completed: status === 'complete' ? 1 : 0,
    failed: status === 'failed' ? 1 : 0,
    book_statuses: [{ title: 'southern.pdf', status }],
    created_at: '2026-03-31T00:00:00Z',
    completed_at: status === 'complete' ? '2026-03-31T00:05:00Z' : null,
  };
}

describe('IngestPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorageMock.clear();
    vi.spyOn(ingestApi, 'listCookbooks').mockResolvedValue(library);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('persists the active ingest job id after upload starts', async () => {
    vi.spyOn(ingestApi, 'uploadPdf').mockResolvedValue({ job_id: 'job-123' });
    vi.spyOn(ingestApi, 'getIngestionStatus').mockResolvedValue(buildJob('processing'));

    render(<IngestPage />);

    const file = new File(['pdf'], 'southern.pdf', { type: 'application/pdf' });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).not.toBeNull();
    await userEvent.upload(fileInput, file);
    await userEvent.click(screen.getByRole('button', { name: 'Upload & Process' }));

    await waitFor(() => expect(ingestApi.uploadPdf).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(window.localStorage.getItem(ACTIVE_INGEST_JOB_KEY)).toBe('job-123'));
    await waitFor(() => expect(ingestApi.getIngestionStatus).toHaveBeenCalledWith('job-123'));
    expect(await screen.findByText('Ingestion Job')).toBeInTheDocument();
  });

  it('rehydrates a stored ingest job id on mount and resumes polling', async () => {
    window.localStorage.setItem(ACTIVE_INGEST_JOB_KEY, 'job-999');
    vi.spyOn(ingestApi, 'getIngestionStatus').mockResolvedValue(buildJob('processing'));

    render(<IngestPage />);

    await waitFor(() => expect(ingestApi.getIngestionStatus).toHaveBeenCalledWith('job-999'));
    expect(await screen.findByText('Ingestion Job')).toBeInTheDocument();
    expect(screen.getAllByText('processing').length).toBeGreaterThan(0);
  });

  it('polls resumed ingest jobs on the configured interval instead of refetching every render', async () => {
    vi.useFakeTimers();
    window.localStorage.setItem(ACTIVE_INGEST_JOB_KEY, 'job-777');
    const getStatus = vi.spyOn(ingestApi, 'getIngestionStatus').mockResolvedValue(buildJob('processing'));

    render(<IngestPage />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2900);
    });
    expect(getStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(getStatus).toHaveBeenCalledTimes(2);
  });

  it('clears persisted ingest state after a terminal job completes', async () => {
    window.localStorage.setItem(ACTIVE_INGEST_JOB_KEY, 'job-123');
    vi.spyOn(ingestApi, 'getIngestionStatus').mockResolvedValue(buildJob('complete'));

    render(<IngestPage />);

    await waitFor(() => expect(ingestApi.getIngestionStatus).toHaveBeenCalledWith('job-123'));
    await waitFor(() => expect(window.localStorage.getItem(ACTIVE_INGEST_JOB_KEY)).toBeNull());
    expect(await screen.findByText('Done! 1 book(s) processed successfully.')).toBeInTheDocument();
  });
});
