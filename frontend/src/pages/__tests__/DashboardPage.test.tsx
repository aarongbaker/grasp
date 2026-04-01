import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DashboardPage } from '../DashboardPage';
import { Sidebar } from '../../components/layout/Sidebar';
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

  it('shows separate creation paths for dinner planning and chef-authored drafting', async () => {
    renderWithAuth(<DashboardPage />);

    expect(screen.getByRole('heading', { name: 'Your Sessions' })).toBeInTheDocument();

    const planLinks = screen.getAllByRole('link', { name: /plan a dinner|open dinner planner/i });
    expect(planLinks.some((link) => link.getAttribute('href') === '/sessions/new')).toBe(true);

    const recipeWorkspaceLink = screen.getByRole('link', { name: /open recipe workspace/i });
    expect(recipeWorkspaceLink).toHaveAttribute('href', '/recipes/new');
    expect(screen.getByRole('heading', { name: 'Start a Recipe Draft' })).toBeInTheDocument();

    await waitFor(() => expect(sessionsApi.listSessions).toHaveBeenCalledWith('user-1'));
  });

  it('keeps the sidebar exposing both protected entry paths', () => {
    renderWithAuth(<Sidebar />, ['/recipes/new']);

    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: /plan a dinner/i })).toHaveAttribute('href', '/sessions/new');
    expect(screen.getByRole('link', { name: /recipe drafts/i })).toHaveAttribute('href', '/recipes/new');
    expect(screen.getByRole('link', { name: /recipe drafts/i }).className).toMatch(/navLinkActive/);
  });
});
