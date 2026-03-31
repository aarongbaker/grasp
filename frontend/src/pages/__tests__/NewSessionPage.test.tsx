import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
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

  it('lands cookbook mode on a chooser first, then submits stable chunk order after entering a book', async () => {
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

    expect(screen.getByRole('heading', { name: 'Choose a cookbook to browse' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Select Burnt Honey Tart')).not.toBeInTheDocument();

    const weeknightCard = screen.getByRole('button', { name: 'Browse Weeknight Classics' });
    expect(within(weeknightCard).getByText('3 recipes')).toBeInTheDocument();
    expect(within(weeknightCard).getByText(/Roast Chicken with Herbs, Slow-Roasted Pork Shoulder, Braised Greens/i)).toBeInTheDocument();

    await userEvent.click(weeknightCard);

    expect(screen.getByRole('heading', { level: 2, name: 'Weeknight Classics' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to cookbook chooser' })).toBeInTheDocument();
    expect(screen.getByLabelText('Select Roast Chicken with Herbs')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Browse The Dessert Atlas' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Select Roast Chicken with Herbs'));
    await userEvent.click(screen.getByRole('button', { name: 'Back to cookbook chooser' }));
    await userEvent.click(screen.getByRole('button', { name: 'Browse The Dessert Atlas' }));
    await userEvent.click(screen.getByLabelText('Select Burnt Honey Tart'));

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

  it('filters recipes within the active cookbook, keeps selections pinned, and restores the full list after reset', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));

    await userEvent.click(screen.getByLabelText('Select Roast Chicken with Herbs'));
    expect(screen.getByText('Your menu')).toBeInTheDocument();
    expect(screen.getByText('1 recipe')).toBeInTheDocument();

    const searchInput = screen.getByLabelText('Search this cookbook');
    await userEvent.type(searchInput, 'greens');

    expect(screen.queryByLabelText('Select Roast Chicken with Herbs')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Select Braised Greens')).toBeInTheDocument();
    expect(screen.getByText('Showing 1 of 3')).toBeInTheDocument();
    expect(screen.getByText('1 matching')).toBeInTheDocument();
    expect(screen.getByText('1 recipe')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Reset search' }));

    expect(searchInput).toHaveValue('');
    expect(screen.getByLabelText('Select Roast Chicken with Herbs')).toBeInTheDocument();
    expect(screen.getByLabelText('Select Braised Greens')).toBeInTheDocument();
    expect(screen.getByText('Showing 3 of 3')).toBeInTheDocument();
    expect(screen.getByText('1 recipe')).toBeInTheDocument();
  });

  it('shows editorial list hierarchy cues for the active cookbook browser', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));

    expect(screen.getByText('Within this cookbook')).toBeInTheDocument();
    expect(screen.getByText(/Search titles, chapters, ingredients, or OCR fragments/i)).toBeInTheDocument();
    expect(screen.getByText('Recipe')).toBeInTheDocument();
    expect(screen.getByText('Source')).toBeInTheDocument();
    expect(screen.getByText('Preview')).toBeInTheDocument();
    expect(screen.getByText('Selections stay pinned while you refine the list.')).toBeInTheDocument();
  });

  it('shows a no-match state with a clear action and keeps the full selected set for submit', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));

    await userEvent.click(screen.getByLabelText('Select Roast Chicken with Herbs'));
    await userEvent.type(screen.getByLabelText('Search this cookbook'), 'custard');

    expect(screen.getByText('No recipes match “custard”.')).toBeInTheDocument();
    expect(screen.getByText(/Your selected recipes are still saved in the menu summary/i)).toBeInTheDocument();
    expect(screen.getByText('1 recipe')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Schedule Selected Recipes' }));

    await waitFor(() => expect(createSessionSpy).toHaveBeenCalledTimes(1));
    expect(createSessionSpy).toHaveBeenCalledWith({
      concept_source: 'cookbook',
      free_text: 'Cookbook-selected recipes: Roast Chicken with Herbs',
      selected_recipes: [{ chunk_id: 'aaa-chunk' }],
      guest_count: 4,
      meal_type: 'dinner',
      occasion: 'dinner_party',
      dietary_restrictions: [],
      serving_time: undefined,
    });
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
  });

  it('matches against OCR-derived preview text and clears search when leaving the active cookbook', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));

    const searchInput = screen.getByLabelText('Search this cookbook');
    await userEvent.type(searchInput, 'tender');

    expect(screen.getByLabelText('Select Slow-Roasted Pork Shoulder')).toBeInTheDocument();
    expect(screen.queryByLabelText('Select Roast Chicken with Herbs')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Back to cookbook chooser' }));
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));

    expect(screen.getByLabelText('Search this cookbook')).toHaveValue('');
    expect(screen.getByLabelText('Select Roast Chicken with Herbs')).toBeInTheDocument();
    expect(screen.getByLabelText('Select Slow-Roasted Pork Shoulder')).toBeInTheDocument();
  });

  it('shows selection summary with removable pills and clear all button across cookbook navigation', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Browse The Dessert Atlas' }));
    await userEvent.click(screen.getByLabelText('Select Burnt Honey Tart'));
    await userEvent.click(screen.getByRole('button', { name: 'Back to cookbook chooser' }));
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));
    await userEvent.click(screen.getByLabelText('Select Roast Chicken with Herbs'));

    expect(screen.getByText('Your menu')).toBeInTheDocument();
    expect(screen.getByText('2 recipes')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Remove Burnt Honey Tart' }));
    expect(screen.getByText('1 recipe')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Clear all selections' }));
    expect(screen.queryByText('Your menu')).not.toBeInTheDocument();
  });

  it('shows a cookbook loading state before chooser cards render', async () => {
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
    expect(screen.getByRole('button', { name: 'Browse Weeknight Classics' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Browse The Dessert Atlas' })).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole('button', { name: 'Browse The Dessert Atlas' }));
    expect(screen.getByLabelText('Select Burnt Honey Tart')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Select Burnt Honey Tart'));
    await userEvent.click(screen.getByRole('button', { name: 'Schedule Selected Recipes' }));

    expect(await screen.findByText('Session creation failed')).toBeInTheDocument();
    expect(runPipelineSpy).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('keeps the native checkbox directly clickable and preserves the mobile stacked selection layout', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));

    const checkbox = screen.getByLabelText('Select Roast Chicken with Herbs');
    await userEvent.click(checkbox);
    expect(checkbox).toBeChecked();

    const option = checkbox.closest('label');
    expect(option).not.toBeNull();
    expect(option?.className).toContain('recipeOption');

    const selectionControl = option?.querySelector('[class*="recipeSelectionControl"]');
    const optionBody = option?.querySelector('[class*="recipeOptionBody"]');
    expect(selectionControl).not.toBeNull();
    expect(optionBody).not.toBeNull();

    await userEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  it('provides progressive disclosure for long recipe excerpts inside the active cookbook view', async () => {
    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(detectedRecipes);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Browse Weeknight Classics' }));

    expect(screen.getByRole('button', { name: 'Show recipe preview' })).toBeInTheDocument();

    const previewButtons = screen.getAllByRole('button', { name: 'Show recipe preview' });
    expect(previewButtons.length).toBeGreaterThan(0);

    await userEvent.click(previewButtons[0]);
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Show less' }));
    expect(screen.queryByRole('button', { name: 'Show less' })).not.toBeInTheDocument();
  });

  it('prefers inferred titles from chunk text before chapter/page fallback for OCR noise names', async () => {
    const recipesWithOcrNoise: DetectedRecipeCandidate[] = [
      {
        chunk_id: 'clean-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: 'Roast Chicken',
        chapter: 'Main Courses',
        page_number: 42,
        text: 'A delicious roast chicken recipe.',
      },
      {
        chunk_id: 'ocr-noise-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: '3.5 oz /',
        chapter: 'Desserts',
        page_number: 88,
        text: 'Burnt Honey Tart\nIngredients\nHoney\nCream',
      },
      {
        chunk_id: 'lowercase-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: 'broken lowercase start',
        chapter: 'Appetizers',
        page_number: 12,
        text: 'Crisp Fennel Salad\nMethod\nSlice thinly.',
      },
      {
        chunk_id: 'empty-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: '',
        chapter: 'Soups',
        page_number: 55,
        text: 'Soup recipe.',
      },
      {
        chunk_id: 'special-char-chunk',
        book_id: 'book-a',
        book_title: 'Test Book',
        recipe_name: '|__Recipe__| Test',
        chapter: 'Salads',
        page_number: 23,
        text: 'Salad recipe.',
      },
    ];

    vi.spyOn(ingestApi, 'listDetectedRecipes').mockResolvedValue(recipesWithOcrNoise);

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Schedule exact uploaded recipes/i }));
    await waitFor(() => expect(screen.queryByText('Loading cookbook recipes…')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Browse Test Book' }));

    expect(screen.getByText('Roast Chicken')).toBeInTheDocument();
    expect(screen.getByText('Burnt Honey Tart')).toBeInTheDocument();
    expect(screen.getByText('Crisp Fennel Salad')).toBeInTheDocument();
    expect(screen.getByText('Soups, p. 55')).toBeInTheDocument();
    expect(screen.getByText('Salads, p. 23')).toBeInTheDocument();

    expect(screen.queryByText('3.5 oz /')).not.toBeInTheDocument();
    expect(screen.queryByText('broken lowercase start')).not.toBeInTheDocument();
    expect(screen.queryByText('|__Recipe__| Test')).not.toBeInTheDocument();
  });
});
