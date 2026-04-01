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
    <MemoryRouter initialEntries={[`/sessions/${menuSession.session_id}`]}>
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

  it('renders menu intent on dashboard cards', () => {
    render(
      <MemoryRouter>
        <SessionCard session={menuSession} />
      </MemoryRouter>,
    );

    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
  });

  it('renders menu context on the session detail page without changing tabs or status flow', async () => {
    renderDetailPage();

    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(menuSession.session_id));
  });

  it('uses the menu intent in the PDF surface', () => {
    render(<RecipePDF session={menuSession} results={results} />);

    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
  });
});
