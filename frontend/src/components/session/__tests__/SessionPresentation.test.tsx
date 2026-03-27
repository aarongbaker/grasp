import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionCard } from '../SessionCard';
import { RecipePDF } from '../RecipePDF';
import { getSessionConceptDisplay } from '../sessionConceptDisplay';
import { SessionDetailPage } from '../../../pages/SessionDetailPage';
import type { Session, SessionResults } from '../../../types/api';
import * as sessionsApi from '../../../api/sessions';
import * as sessionStatusHook from '../../../hooks/useSessionStatus';

vi.mock('@react-pdf/renderer', async () => {
  const actual = await vi.importActual<typeof import('@react-pdf/renderer')>('@react-pdf/renderer');
  const passthrough = ({ children }: { children?: React.ReactNode }) => <>{children}</>;
  const text = ({ children }: { children?: React.ReactNode }) => <span>{children}</span>;

  return {
    ...actual,
    Document: passthrough,
    Page: passthrough,
    View: passthrough,
    Text: text,
    Font: { register: vi.fn() },
    StyleSheet: { create: <T,>(styles: T) => styles },
  };
});

const cookbookSession: Session = {
  session_id: 'session-cookbook',
  user_id: 'user-1',
  status: 'complete',
  concept_json: {
    free_text: 'Cookbook-selected recipes: Roast Chicken, Braised Greens',
    guest_count: 4,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: null,
    concept_source: 'cookbook',
    selected_recipes: [
      {
        chunk_id: 'chunk-1',
        book_id: 'book-1',
        book_title: 'Weeknight Classics',
        chapter: 'Centerpieces',
        page_number: 42,
        text: 'Roast Chicken with Herbs\nPat dry and season generously.',
      },
      {
        chunk_id: 'chunk-2',
        book_id: 'book-1',
        book_title: 'Weeknight Classics',
        chapter: 'Sides',
        page_number: 117,
        text: 'Braised Greens\nWilt greens with garlic and stock.',
      },
    ],
  },
  schedule_summary: 'Dinner lands all at once.',
  total_duration_minutes: 95,
  error_summary: null,
  result_recipes: null,
  result_schedule: null,
  token_usage: null,
  created_at: '2026-03-27T00:00:00Z',
  started_at: '2026-03-27T00:01:00Z',
  completed_at: '2026-03-27T00:30:00Z',
};

const legacyCookbookSession: Session = {
  ...cookbookSession,
  session_id: 'session-legacy-cookbook',
  concept_json: {
    ...cookbookSession.concept_json,
    free_text: 'A fallback dinner from uploaded books',
    selected_recipes: [],
  },
};

const freeTextSession: Session = {
  ...cookbookSession,
  session_id: 'session-free-text',
  concept_json: {
    free_text: 'A bright spring dinner party with fish and citrus',
    guest_count: 6,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: null,
    concept_source: 'free_text',
    selected_recipes: [],
  },
};

const results: SessionResults = {
  schedule: {
    timeline: [],
    total_duration_minutes: 95,
    total_duration_minutes_max: null,
    active_time_minutes: 70,
    summary: 'Dinner lands all at once.',
    error_summary: null,
  },
  recipes: [],
  errors: [],
};

function renderDetailPage() {
  return render(
    <MemoryRouter initialEntries={[`/sessions/${cookbookSession.session_id}`]}>
      <Routes>
        <Route path="/sessions/:sessionId" element={<SessionDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('session presentation', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(sessionStatusHook, 'useSessionStatus').mockReturnValue({
      data: cookbookSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });
    vi.spyOn(sessionsApi, 'getSessionResults').mockResolvedValue(results);
  });

  afterEach(() => {
    cleanup();
  });

  it('builds cookbook-aware display metadata from selected recipes', () => {
    expect(getSessionConceptDisplay(cookbookSession.concept_json)).toEqual(
      expect.objectContaining({
        isCookbook: true,
        sourceLabel: 'Cookbook menu',
        sourceDetail: 'From Weeknight Classics',
        recipeSummary: 'Roast Chicken with Herbs and Braised Greens',
        title: 'Cookbook menu: Roast Chicken with Herbs and Braised Greens',
        recipeCount: 2,
      }),
    );
  });

  it('falls back safely for legacy cookbook sessions without selected recipes', () => {
    expect(getSessionConceptDisplay(legacyCookbookSession.concept_json)).toEqual(
      expect.objectContaining({
        isCookbook: true,
        title: 'A fallback dinner from uploaded books',
        recipeSummary: 'A fallback dinner from uploaded books',
        sourceDetail: 'Cookbook-selected session',
        recipeCount: 0,
      }),
    );
  });

  it('keeps free-text sessions on the original meal-idea presentation path', () => {
    expect(getSessionConceptDisplay(freeTextSession.concept_json)).toEqual(
      expect.objectContaining({
        isCookbook: false,
        title: 'A bright spring dinner party with fish and citrus',
        sourceLabel: 'Meal idea',
        sourceDetail: null,
        recipeSummary: null,
        recipeCount: 0,
        recipeNames: [],
        cookbookTitles: [],
      }),
    );
  });

  it('renders cookbook labels and metadata on dashboard cards', () => {
    render(
      <MemoryRouter>
        <SessionCard session={cookbookSession} />
      </MemoryRouter>,
    );

    expect(screen.getByText('Cookbook menu')).toBeInTheDocument();
    expect(screen.getByText('Cookbook menu: Roast Chicken with Herbs and Braised Greens')).toBeInTheDocument();
    expect(screen.getByText('From Weeknight Classics')).toBeInTheDocument();
    expect(screen.getAllByText('Roast Chicken with Herbs and Braised Greens')[0]).toBeInTheDocument();
  });

  it('renders cookbook context on the session detail page without changing tabs or status flow', async () => {
    renderDetailPage();

    expect(screen.getByText('Cookbook menu')).toBeInTheDocument();
    expect(screen.getByText('From Weeknight Classics')).toBeInTheDocument();
    expect(screen.getByText('Cookbook menu: Roast Chicken with Herbs and Braised Greens')).toBeInTheDocument();
    expect(screen.getByText('Selected recipes: Roast Chicken with Herbs and Braised Greens')).toBeInTheDocument();
    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(cookbookSession.session_id));
  });

  it('uses the normalized cookbook title in the PDF surface', () => {
    render(<RecipePDF session={cookbookSession} results={results} />);

    expect(screen.getByText('Cookbook menu: Roast Chicken with Herbs and Braised Greens')).toBeInTheDocument();
    expect(screen.getByText('From Weeknight Classics')).toBeInTheDocument();
  });
});
