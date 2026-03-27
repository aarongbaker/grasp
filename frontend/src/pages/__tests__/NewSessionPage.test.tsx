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
  {
    chunk_id: 'bbb-chunk',
    book_id: 'book-a',
    book_title: 'Weeknight Classics',
    recipe_name: 'Slow-Roasted Pork Shoulder',
    chapter: 'Centerpieces',
    page_number: 58,
    text: 'Season generously with salt and pepper the night before. Let the meat come to room temperature before roasting. Start at high heat to develop a crust, then lower to 275°F and roast slowly for 6-8 hours until the internal temperature reaches 195°F and the meat is fall-apart tender. Rest for at least 30 minutes before shredding.',
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
    const callOrder: string[] = [];
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockImplementation(async (payload) => {
      callOrder.push(`create:${JSON.stringify(payload)}`);
      return createdSession;
    });
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockImplementation(async (sessionId) => {
      callOrder.push(`run:${sessionId}`);
      return {
        session_id: 'session-123',
        status: 'generating',
        message: 'Pipeline enqueued',
      };
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
    expect(callOrder).toEqual([
      'create:{"free_text":"A bright spring dinner","guest_count":4,"meal_type":"dinner","occasion":"dinner_party","dietary_restrictions":[]}',
      'run:session-123',
    ]);
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
    expect(detectedSpy).not.toHaveBeenCalled();
  });

  it('loads cookbook candidates lazily, supports mixed-book selection, and submits stable chunk order', async () => {
    const callOrder: string[] = [];
    const detectedSpy = vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockImplementation(async (payload) => {
      callOrder.push(`create:${JSON.stringify(payload)}`);
      return createdSession;
    });
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockImplementation(async (sessionId) => {
      callOrder.push(`run:${sessionId}`);
      return {
        session_id: 'session-123',
        status: 'generating',
        message: 'Pipeline enqueued',
      };
    });

    renderPage();

    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));

    await waitFor(() => expect(detectedSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Weeknight Classics')).toBeInTheDocument();
    expect(screen.getByLabelText('The Dessert Atlas')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Select Burnt Honey Tart'));
    await userEvent.click(screen.getByLabelText('Select Roast Chicken with Herbs'));

    // Verify selection summary appears with selected recipes
    expect(screen.getByText('Your menu')).toBeInTheDocument();
    expect(screen.getByText('2 recipes')).toBeInTheDocument();

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
    expect(callOrder).toEqual([
      'create:{"concept_source":"cookbook","free_text":"Cookbook-selected recipes: Roast Chicken with Herbs, Burnt Honey Tart","selected_recipes":[{"chunk_id":"aaa-chunk"},{"chunk_id":"zzz-chunk"}],"guest_count":4,"meal_type":"dinner","occasion":"dinner_party","dietary_restrictions":[]}',
      'run:session-123',
    ]);
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
  });

  it('shows selection summary with removable pills and clear all button', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());

    // Select two recipes
    await userEvent.click(screen.getByLabelText('Select Burnt Honey Tart'));
    await userEvent.click(screen.getByLabelText('Select Roast Chicken with Herbs'));

    // Verify selection summary appears
    expect(screen.getByText('Your menu')).toBeInTheDocument();
    expect(screen.getByText('2 recipes')).toBeInTheDocument();

    // Verify individual remove buttons work
    await userEvent.click(screen.getByRole('button', { name: 'Remove Burnt Honey Tart' }));
    expect(screen.getByText('1 recipe')).toBeInTheDocument();

    // Verify clear all works
    await userEvent.click(screen.getByRole('button', { name: 'Clear all selections' }));
    expect(screen.queryByText('Your menu')).not.toBeInTheDocument();
  });

  it('shows a cookbook loading state before candidates render', async () => {
    let resolveRecipes: ((value: DetectedRecipeCandidate[]) => void) | undefined;
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveRecipes = resolve;
      }),
    );

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));

    expect(screen.getByText('Loading cookbook recipes…')).toBeInTheDocument();
    resolveRecipes?.(detectedRecipes);
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

  it('provides progressive disclosure for long recipe excerpts', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());

    // Short excerpts should not have a show more button
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeInTheDocument();

    // Long excerpt should be truncated with show more button
    const showMoreButtons = screen.getAllByRole('button', { name: 'Show more' });
    expect(showMoreButtons.length).toBeGreaterThan(0);

    // Click to expand
    await userEvent.click(showMoreButtons[0]);
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();

    // Clicking show less should collapse
    await userEvent.click(screen.getByRole('button', { name: 'Show less' }));
    expect(screen.queryByRole('button', { name: 'Show less' })).not.toBeInTheDocument();
  });

  it('falls back to chapter/page labels for OCR noise recipe names', async () => {
    const recipesWithOcrNoise: DetectedRecipeCandidate[] = [
      {
        chunk_id: 'clean-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: 'Roast Chicken', // Good title
        chapter: 'Main Courses',
        page_number: 42,
        text: 'A delicious roast chicken recipe.',
      },
      {
        chunk_id: 'ocr-noise-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: '3.5 oz /', // OCR noise: starts with number, short, odd characters
        chapter: 'Desserts',
        page_number: 88,
        text: 'Some dessert recipe text.',
      },
      {
        chunk_id: 'lowercase-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: 'broken lowercase start', // starts with lowercase
        chapter: 'Appetizers',
        page_number: 12,
        text: 'Appetizer recipe.',
      },
      {
        chunk_id: 'empty-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: '', // Empty
        chapter: 'Soups',
        page_number: 55,
        text: 'Soup recipe.',
      },
      {
        chunk_id: 'special-char-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: '|__Recipe__| Test', // OCR garbage characters
        chapter: 'Salads',
        page_number: 23,
        text: 'Salad recipe.',
      },
    ];

    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(recipesWithOcrNoise);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());

    // Good title should be used as-is
    expect(screen.getByText('Roast Chicken')).toBeInTheDocument();

    // OCR noise should fall back to chapter + page
    expect(screen.getByText('Desserts, p. 88')).toBeInTheDocument();
    expect(screen.getByText('Appetizers, p. 12')).toBeInTheDocument();
    expect(screen.getByText('Soups, p. 55')).toBeInTheDocument();
    expect(screen.getByText('Salads, p. 23')).toBeInTheDocument();

    // The original OCR noise should NOT be displayed as the primary title
    expect(screen.queryByText('3.5 oz /')).not.toBeInTheDocument();
    expect(screen.queryByText('broken lowercase start')).not.toBeInTheDocument();
    expect(screen.queryByText('|__Recipe__| Test')).not.toBeInTheDocument();
  });
});
