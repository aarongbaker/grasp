import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DashboardPage } from '../DashboardPage';
import { Sidebar } from '../../components/layout/Sidebar';
import { PATHWAYS } from '../../components/layout/pathways';
import { AuthContext } from '../../context/auth-context';
import * as sessionsApi from '../../api/sessions';
import type { Session, UserProfile } from '../../types/api';

const mockLogout = vi.fn();
const mockSetUser = vi.fn();

const authValue = {
  token: 'token',
  userId: 'user-1',
  user: {
    user_id: 'user-1',
    email: 'chef@example.com',
    name: 'Chef Mira',
    kitchen_config_id: null,
    kitchen_config: null,
    dietary_defaults: [],
    equipment: [],
    created_at: '2026-04-01T00:00:00Z',
  } satisfies UserProfile,
  isAuthenticated: true,
  login: vi.fn(),
  logout: mockLogout,
  setUser: mockSetUser,
};

const sessions: Session[] = [
  {
    session_id: 'session-1',
    user_id: 'user-1',
    status: 'pending',
    concept_json: {
      free_text: 'A spring dinner with lamb and peas',
      guest_count: 6,
      meal_type: 'dinner',
      occasion: 'dinner_party',
      dietary_restrictions: [],
      serving_time: '18:30',
    },
    schedule_summary: null,
    total_duration_minutes: null,
    error_summary: null,
    result_recipes: null,
    result_schedule: null,
    token_usage: null,
    created_at: '2026-04-01T00:00:00Z',
    started_at: null,
    completed_at: null,
  },
];

function renderWithAuth(ui: React.ReactNode, initialEntries: string[] = ['/']) {
  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe('DashboardPage discoverability', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockLogout.mockReset();
    mockSetUser.mockReset();
    vi.spyOn(sessionsApi, 'listSessions').mockResolvedValue(sessions);
    vi.spyOn(sessionsApi, 'deleteSession').mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
  });

  it('shows separate creation paths for dinner planning, private library browsing, and chef-authored drafting', async () => {
    renderWithAuth(<DashboardPage />);

    expect(screen.getByRole('heading', { name: 'Your Sessions' })).toBeInTheDocument();

    for (const pathway of PATHWAYS) {
      expect(screen.getByRole('heading', { name: pathway.title })).toBeInTheDocument();
      expect(screen.getByText(pathway.purpose)).toBeInTheDocument();
      expect(screen.getByText(pathway.relationship)).toBeInTheDocument();
      expect(screen.getByRole('link', { name: pathway.cta })).toHaveAttribute('href', pathway.to);
    }

    const planLinks = screen.getAllByRole('link', { name: /plan a dinner|open dinner planner/i });
    expect(planLinks.some((link) => link.getAttribute('href') === '/sessions/new')).toBe(true);

    await waitFor(() => expect(sessionsApi.listSessions).toHaveBeenCalledWith('user-1'));
  });

  it('keeps the sidebar exposing the library and draft entry paths separately', () => {
    renderWithAuth(<Sidebar />, ['/recipes']);

    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/');
    for (const pathway of PATHWAYS) {
      expect(screen.getByRole('link', { name: new RegExp(pathway.navLabel, 'i') })).toHaveAttribute('href', pathway.to);
    }
    expect(screen.getByRole('link', { name: /recipe library/i }).className).toMatch(/navLinkActive/);
  });
});
