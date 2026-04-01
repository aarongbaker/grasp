import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionCard } from '../SessionCard';
import { RecipePDF } from '../RecipePDF';
import { getSessionConceptDisplay } from '../sessionConceptDisplay';
import { SessionDetailPage } from '../../../pages/SessionDetailPage';
import type { DinnerConcept, Session, SessionResults } from '../../../types/api';
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

const menuSession: Session = {
  session_id: 'session-menu-intent',
  user_id: 'user-1',
  status: 'complete',
  concept_json: {
    free_text: 'A rustic Italian dinner with handmade pasta and seasonal vegetables',
    guest_count: 4,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: null,
    concept_source: 'free_text',
    selected_recipes: [],
    selected_authored_recipe: null,
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

const freeTextSession: Session = {
  ...menuSession,
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
    selected_authored_recipe: null,
  },
};

const authoredSession: Session = {
  ...menuSession,
  session_id: 'session-authored',
  concept_json: {
    free_text: 'Schedule the private-library chicken ballotine for Saturday service',
    guest_count: 8,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: '19:30',
    concept_source: 'authored',
    selected_recipes: [],
    selected_authored_recipe: {
      recipe_id: 'recipe-authored-1',
      title: 'Chicken Ballotine with Tarragon Jus',
    },
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

function renderDetailPage(sessionId: string = menuSession.session_id) {
  return render(
    <MemoryRouter initialEntries={[`/sessions/${sessionId}`]}>
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
      data: menuSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });
    vi.spyOn(sessionsApi, 'getSessionResults').mockResolvedValue(results);
  });

  afterEach(() => {
    cleanup();
  });

  it('builds display metadata from menu intent', () => {
    expect(getSessionConceptDisplay(menuSession.concept_json)).toEqual({
      title: 'A rustic Italian dinner with handmade pasta and seasonal vegetables',
    });
  });

  it('keeps free-text sessions on the original meal-idea presentation path', () => {
    expect(getSessionConceptDisplay(freeTextSession.concept_json)).toEqual({
      title: 'A bright spring dinner party with fish and citrus',
    });
  });

  it('prefers the authored recipe title for authored sessions', () => {
    expect(getSessionConceptDisplay(authoredSession.concept_json)).toEqual({
      title: 'Chicken Ballotine with Tarragon Jus',
    });
  });

  it('falls back to free text when an authored payload is missing the trusted title', () => {
    const malformedConcept: DinnerConcept = {
      ...authoredSession.concept_json,
      free_text: 'Fallback authored planning note',
      selected_authored_recipe: {
        recipe_id: 'recipe-authored-1',
        title: '   ',
      },
    };

    expect(getSessionConceptDisplay(malformedConcept)).toEqual({
      title: 'Fallback authored planning note',
    });
  });

  it('falls back to a generic session label when no authored title or free text exists', () => {
    const malformedConcept: DinnerConcept = {
      ...authoredSession.concept_json,
      free_text: '   ',
      selected_authored_recipe: null,
    };

    expect(getSessionConceptDisplay(malformedConcept)).toEqual({
      title: 'Dinner session',
    });
  });

  it('renders menu intent on dashboard cards', () => {
    render(
      <MemoryRouter>
        <SessionCard session={menuSession} />
      </MemoryRouter>,
    );

    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
  });

  it('renders authored recipe titles on dashboard cards', () => {
    render(
      <MemoryRouter>
        <SessionCard session={authoredSession} />
      </MemoryRouter>,
    );

    expect(screen.getByText('Chicken Ballotine with Tarragon Jus')).toBeInTheDocument();
  });

  it('renders menu context on the session detail page without changing tabs or status flow', async () => {
    renderDetailPage();

    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(menuSession.session_id));
  });

  it('renders authored recipe titles on the session detail page', async () => {
    vi.spyOn(sessionStatusHook, 'useSessionStatus').mockReturnValue({
      data: authoredSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });

    renderDetailPage(authoredSession.session_id);

    expect(screen.getByText('Chicken Ballotine with Tarragon Jus')).toBeInTheDocument();
    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(authoredSession.session_id));
  });

  it('keeps the existing detail retry banner when result fetching fails', async () => {
    vi.spyOn(sessionsApi, 'getSessionResults').mockRejectedValue(new Error('Results unavailable'));

    renderDetailPage();

    expect(await screen.findByText('Could not load results')).toBeInTheDocument();
    expect(screen.getByText('Results unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('uses the menu intent in the PDF surface', () => {
    render(<RecipePDF session={menuSession} results={results} />);

    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
  });

  it('uses the authored recipe title in the PDF surface', () => {
    render(<RecipePDF session={authoredSession} results={results} />);

    expect(screen.getByText('Chicken Ballotine with Tarragon Jus')).toBeInTheDocument();
  });
});
