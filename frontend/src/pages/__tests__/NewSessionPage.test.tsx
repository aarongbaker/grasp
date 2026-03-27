import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewSessionPage } from '../NewSessionPage';
import * as ingestApi from '../../api/ingest';
import * as sessionsApi from '../../api/sessions';
import type { DetectedRecipeCandidate, Session } from '../../types/api';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <NewSessionPage />
    </MemoryRouter>,
  );
}

const detectedRecipes: DetectedRecipeCandidate[] = [
  {
    chunk_id: 'zzz-chunk',
    book_id: 'book-b',
    book_title: 'The Dessert Atlas',
    recipe_name: 'Burnt Honey Tart',
    chapter: 'Late Course',
    page_number: 88,
    text: 'Blind bake the crust, warm the honey, and finish with flaky salt.',
  },
  {
    chunk_id: 'aaa-chunk',
    book_id: 'book-a',
    book_title: 'Weeknight Classics',
    recipe_name: 'Roast Chicken with Herbs',
    chapter: 'Centerpieces',
    page_number: 42,
    text: 'Dry the bird overnight, roast hot, then rest before carving.',
  },
  {
    chunk_id: 'mmm-chunk',
    book_id: 'book-a',
    book_title: 'Weeknight Classics',
    recipe_name: 'Braised Greens',
    chapter: 'Sides',
    page_number: 117,
    text: 'Wilt the greens, add stock, and simmer until tender.',
  },
];

const createdSession: Session = {
  session_id: 'session-123',
  user_id: 'user-1',
  status: 'pending',
  concept_json: {
    free_text: 'Cookbook-selected recipes: Roast Chicken with Herbs, Burnt Honey Tart',
    guest_count: 4,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: null,
    concept_source: 'cookbook',
    selected_recipes: [],
  },
  schedule_summary: null,
  total_duration_minutes: null,
  error_summary: null,
  result_recipes: null,
  result_schedule: null,
  token_usage: null,
  created_at: '2026-03-27T00:00:00Z',
  started_at: null,
  completed_at: null,
};

describe('NewSessionPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('keeps the meal-idea flow isolated and submits the legacy payload', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });
    const detectedSpy = vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A bright spring dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() => expect(createSessionSpy).toHaveBeenCalledTimes(1));
    expect(createSessionSpy).toHaveBeenCalledWith({
      free_text: 'A bright spring dinner',
      guest_count: 4,
      meal_type: 'dinner',
      occasion: 'dinner_party',
      dietary_restrictions: [],
      serving_time: undefined,
    });
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
    expect(detectedSpy).not.toHaveBeenCalled();
  });

  it('loads cookbook candidates lazily, supports mixed-book selection, and submits stable chunk order', async () => {
    const detectedSpy = vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));

    await waitFor(() => expect(detectedSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Weeknight Classics')).toBeInTheDocument();
    expect(screen.getByLabelText('The Dessert Atlas')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Select Burnt Honey Tart'));
    await userEvent.click(screen.getByLabelText('Select Roast Chicken with Herbs'));
    await userEvent.click(screen.getByRole('button', { name: 'Schedule Selected Recipes' }));

    await waitFor(() => expect(createSessionSpy).toHaveBeenCalledTimes(1));
    expect(createSessionSpy).toHaveBeenCalledWith({
      concept_source: 'cookbook',
      free_text: 'Cookbook-selected recipes: Roast Chicken with Herbs, Burnt Honey Tart',
      selected_recipes: [
        { chunk_id: 'aaa-chunk' },
        { chunk_id: 'zzz-chunk' },
      ],
      guest_count: 4,
      meal_type: 'dinner',
      occasion: 'dinner_party',
      dietary_restrictions: [],
      serving_time: undefined,
    });
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
  });

  it('shows a cookbook loading state before candidates render', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockImplementationOnce(
      () => new Promise((resolve) => setTimeout(() => resolve(detectedRecipes), 0)),
    );

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));

    expect(screen.getByText('Loading cookbook recipes…')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Weeknight Classics')).toBeInTheDocument();
    expect(screen.getByText('Burnt Honey Tart')).toBeInTheDocument();
  });

  it('shows cookbook empty and error states inline without breaking the rest of the page', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValueOnce([]);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    expect(screen.getByText(/No detected cookbook recipes yet/i)).toBeInTheDocument();

    cleanup();
    navigateMock.mockReset();
    vi.restoreAllMocks();

    vi.spyOn(ingestApi, 'listDetectedRecipes').mockRejectedValueOnce(new Error('Cookbook fetch failed'));
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    expect(await screen.findByText('Cookbook fetch failed')).toBeInTheDocument();
    expect(screen.getByLabelText('Guests')).toBeInTheDocument();
  });

  it('surfaces cookbook submit failures in page-level error UI', async () => {
    const detectedSpy = vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);
    vi.spyOn(sessionsApi, 'createSession').mockRejectedValue(new Error('Session creation failed'));
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(detectedSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    expect(screen.getByText('Burnt Honey Tart')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Select Burnt Honey Tart'));
    await userEvent.click(screen.getByRole('button', { name: 'Schedule Selected Recipes' }));

    expect(await screen.findByText('Session creation failed')).toBeInTheDocument();
    expect(runPipelineSpy).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
