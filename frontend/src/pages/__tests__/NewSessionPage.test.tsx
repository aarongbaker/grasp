import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NewSessionPage } from '../NewSessionPage';

const navigateMock = vi.fn();
const createSessionMock = vi.fn();
const runPipelineMock = vi.fn();
const listDetectedCookbookRecipesMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../api/sessions', () => ({
  createSession: (...args: unknown[]) => createSessionMock(...args),
  runPipeline: (...args: unknown[]) => runPipelineMock(...args),
}));

vi.mock('../../api/ingest', () => ({
  listDetectedCookbookRecipes: (...args: unknown[]) => listDetectedCookbookRecipesMock(...args),
}));

describe('NewSessionPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    createSessionMock.mockReset();
    runPipelineMock.mockReset();
    listDetectedCookbookRecipesMock.mockReset();
    createSessionMock.mockResolvedValue({ session_id: 'session-123' });
    runPipelineMock.mockResolvedValue({ session_id: 'session-123', status: 'generating', message: 'started' });
    listDetectedCookbookRecipesMock.mockResolvedValue([
      {
        chunk_id: 'chunk-1',
        book_id: 'book-1',
        book_title: 'Sunday Suppers',
        text: 'Braised fennel with lemon, thyme, and cream.',
        chunk_type: 'recipe_candidate',
        chapter: 'Winter Vegetables',
        page_number: 42,
        created_at: '2026-03-26T12:00:00Z',
      },
      {
        chunk_id: 'chunk-2',
        book_id: 'book-1',
        book_title: 'Sunday Suppers',
        text: 'Roast chicken with shallot pan sauce and torn herbs.',
        chunk_type: 'recipe_candidate',
        chapter: 'Main Courses',
        page_number: 88,
        created_at: '2026-03-26T12:00:00Z',
      },
      {
        chunk_id: 'chunk-3',
        book_id: 'book-2',
        book_title: 'The Dessert Shelf',
        text: 'Bittersweet chocolate tart with olive oil crust and sea salt.',
        chunk_type: 'recipe_candidate',
        chapter: 'Tarts',
        page_number: 17,
        created_at: '2026-03-26T12:00:00Z',
      },
    ]);
  });

  function renderPage() {
    return render(
      <MemoryRouter>
        <NewSessionPage />
      </MemoryRouter>,
    );
  }

  it('defaults to meal-idea mode and preserves the legacy submit path', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText(/what are you cooking\?/i), {
      target: { value: 'A rustic Italian dinner with handmade pasta.' },
    });
    fireEvent.change(screen.getByLabelText(/^guests$/i), { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText(/serving time/i), { target: { value: '18:30' } });
    fireEvent.change(screen.getByLabelText(/dietary restrictions/i), { target: { value: 'Vegetarian' } });
    fireEvent.keyDown(screen.getByLabelText(/dietary restrictions/i), { key: 'Enter' });

    fireEvent.click(screen.getByRole('button', { name: /start planning/i }));

    await waitFor(() => {
      expect(createSessionMock).toHaveBeenCalledWith({
        free_text: 'A rustic Italian dinner with handmade pasta.',
        guest_count: 6,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: ['Vegetarian'],
        serving_time: '18:30',
      });
    });
    expect(runPipelineMock).toHaveBeenCalledWith('session-123');
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
    expect(listDetectedCookbookRecipesMock).not.toHaveBeenCalled();
  });

  it('loads and groups cookbook candidates only after switching modes', async () => {
    renderPage();

    expect(listDetectedCookbookRecipesMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: /cookbook recipes/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /sunday suppers/i })).toBeInTheDocument();
    });
    expect(screen.getByRole('heading', { name: /the dessert shelf/i })).toBeInTheDocument();
    expect(listDetectedCookbookRecipesMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/this picker prepares a stable selection payload now/i)).toBeInTheDocument();
    expect(screen.getByText(/cookbook session creation lands in s03/i)).toBeInTheDocument();
  });

  it('supports mixed-book selection, ordering, and deselection in the visible payload summary', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('tab', { name: /cookbook recipes/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /sunday suppers/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText(/select chunk-1 from sunday suppers/i));
    fireEvent.click(screen.getByLabelText(/select chunk-3 from the dessert shelf/i));

    expect(screen.getByText(/2 recipes selected across 2 books/i)).toBeInTheDocument();
    const selectionList = screen.getByRole('list', { name: /selected cookbook chunks/i });
    const items = within(selectionList).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText('Sunday Suppers')).toBeInTheDocument();
    expect(within(items[0]).getByText('Winter Vegetables • Page 42')).toBeInTheDocument();
    expect(within(items[0]).getByText('#1')).toBeInTheDocument();
    expect(within(items[0]).getByText('chunk-1')).toBeInTheDocument();
    expect(within(items[1]).getByText('The Dessert Shelf')).toBeInTheDocument();
    expect(within(items[1]).getByText('Tarts • Page 17')).toBeInTheDocument();
    expect(within(items[1]).getByText('#2')).toBeInTheDocument();
    expect(within(items[1]).getByText('chunk-3')).toBeInTheDocument();
    expect(screen.getByText('Selections')).toBeInTheDocument();
    expect(screen.getByText('Books')).toBeInTheDocument();
    expect(screen.getAllByText('2')).toHaveLength(2);
    expect(screen.getByRole('button', { name: /start cookbook session/i })).toBeEnabled();
    expect(screen.getByText(/your selected cookbook payload is ready/i)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/select chunk-1 from sunday suppers/i));

    expect(screen.getByText(/1 recipes selected across 1 books/i)).toBeInTheDocument();
    expect(within(selectionList).queryByText('chunk-1')).not.toBeInTheDocument();
    expect(within(selectionList).getByText('#1')).toBeInTheDocument();
    expect(within(selectionList).getByText('chunk-3')).toBeInTheDocument();
  });

  it('keeps cookbook submission guarded with explicit copy until cookbook creation exists', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('tab', { name: /cookbook recipes/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /browse cookbook recipes/i })).toBeInTheDocument();
    });

    const submitButton = screen.getByRole('button', { name: /start cookbook session/i });
    expect(submitButton).toBeDisabled();
    expect(submitButton).toHaveAttribute('aria-describedby', 'cookbook-submit-guard');
    expect(screen.getByText(/select at least one cookbook recipe to continue/i)).toBeInTheDocument();
    expect(screen.getByText(/backend cookbook session creation arrives in s03/i)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/select chunk-2 from sunday suppers/i));

    expect(submitButton).toBeEnabled();
    expect(screen.getByText(/your selected cookbook payload is ready/i)).toBeInTheDocument();
    expect(createSessionMock).not.toHaveBeenCalled();
    expect(runPipelineMock).not.toHaveBeenCalled();
  });

  it('surfaces picker-only empty and error states inline without blanking the page', async () => {
    listDetectedCookbookRecipesMock.mockResolvedValueOnce([]);
    renderPage();

    fireEvent.click(screen.getByRole('tab', { name: /cookbook recipes/i }));

    await waitFor(() => {
      expect(screen.getByText(/no detected cookbook recipes yet/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();

    listDetectedCookbookRecipesMock.mockRejectedValueOnce(new Error('Cookbook service unavailable'));
    renderPage();

    fireEvent.click(screen.getAllByRole('tab', { name: /cookbook recipes/i })[1]);

    expect(await screen.findByRole('alert')).toHaveTextContent(/cookbook service unavailable/i);
    expect(screen.getAllByRole('heading', { name: /browse cookbook recipes/i })[1]).toBeInTheDocument();
  });
});
