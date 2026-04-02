import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as authoredRecipesApi from '../../api/authoredRecipes';
import * as recipeCookbooksApi from '../../api/recipeCookbooks';
import * as sessionsApi from '../../api/sessions';
import { AuthContext } from '../../context/auth-context';
import { RecipeLibraryPage } from '../RecipeLibraryPage';
import type { UserProfile } from '../../types/api';

const mockLogout = vi.fn();
const mockSetUser = vi.fn();
const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

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

function renderWithAuth() {
  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter>
        <RecipeLibraryPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe('RecipeLibraryPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockLogout.mockReset();
    mockSetUser.mockReset();
    mockNavigate.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows the empty-state library surface with drafting and planner guidance', async () => {
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([]);
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);

    renderWithAuth();

    expect(screen.getByLabelText('Loading recipe library')).toBeInTheDocument();

    expect(await screen.findByRole('heading', { name: 'No saved dishes yet.' })).toBeInTheDocument();
    expect(screen.getByText(/Draft here\. Plan there\./i)).toBeInTheDocument();
    const draftLinks = screen.getAllByRole('link', { name: /Start a Recipe Draft/i });
    expect(draftLinks.some((link) => link.getAttribute('href') === '/recipes/new')).toBe(true);
    expect(screen.getByRole('link', { name: 'Planning a whole dinner instead?' })).toHaveAttribute('href', '/sessions/new');
    expect(screen.queryByText(/No data found/i)).not.toBeInTheDocument();
  });

  it('keeps the pathway guidance and authored scheduling CTAs visible on the library surface', async () => {
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);

    renderWithAuth();

    expect(await screen.findByText(/Use the shelf when a dish already exists/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Need a full service plan instead?' })).toHaveAttribute('href', '/sessions/new');
    expect(screen.getByRole('button', { name: 'Schedule from shelf' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open recipe workspace/i })).toHaveAttribute('href', '/recipes/new');
  });

  it('groups saved recipes into cookbook folders and leaves unassigned drafts visible', async () => {
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([
      {
        cookbook_id: 'cookbook-dessert',
        user_id: 'user-1',
        name: 'Dessert',
        description: 'Sweet finishes and plated fruit.',
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-01T00:00:00Z',
      },
    ]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-1',
        user_id: 'user-1',
        title: 'Olive oil cake',
        cuisine: 'Italian',
        cookbook_id: 'cookbook-dessert',
        cookbook: {
          cookbook_id: 'cookbook-dessert',
          name: 'Dessert',
          description: 'Sweet finishes and plated fruit.',
        },
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-02T00:00:00Z',
      },
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);

    renderWithAuth();

    expect(await screen.findByRole('heading', { name: 'Unassigned recipes' })).toBeInTheDocument();
    expect(screen.getByText('Marinated peppers')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Dessert' })).toBeInTheDocument();
    expect(screen.getByText('Olive oil cake')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /reopen in workspace/i })[0]).toHaveAttribute(
      'href',
      '/recipes/new?recipeId=recipe-2',
    );
    expect(screen.getByRole('button', { name: 'Schedule from shelf' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Schedule service' })).toBeInTheDocument();
  });

  it('creates cookbooks and moves recipes into them with inline recovery on failure', async () => {
    const user = userEvent.setup();
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);

    const createCookbookSpy = vi.spyOn(recipeCookbooksApi, 'createRecipeCookbook').mockResolvedValue({
      cookbook_id: 'cookbook-mexican',
      user_id: 'user-1',
      name: 'Mexican',
      description: 'Regional dishes and masa work.',
      created_at: '2026-04-01T00:00:00Z',
      updated_at: '2026-04-01T00:00:00Z',
    });
    const moveSpy = vi
      .spyOn(authoredRecipesApi, 'updateAuthoredRecipeCookbook')
      .mockRejectedValueOnce(new Error('Could not move that recipe just now.'))
      .mockResolvedValueOnce({
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        description: 'Bright peppers for the station.',
        cuisine: 'Spanish',
        cookbook_id: 'cookbook-mexican',
        cookbook: {
          cookbook_id: 'cookbook-mexican',
          name: 'Mexican',
          description: 'Regional dishes and masa work.',
        },
        yield_info: { quantity: 4, unit: 'plates', notes: null },
        ingredients: [],
        steps: [],
        equipment_notes: [],
        storage: null,
        hold: null,
        reheat: null,
        make_ahead_guidance: null,
        plating_notes: null,
        chef_notes: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-04T00:00:00Z',
      });

    renderWithAuth();

    await screen.findByText('Marinated peppers');

    await user.type(screen.getByLabelText('Cookbook name'), 'Mexican');
    await user.type(screen.getByLabelText('What belongs here?'), 'Regional dishes and masa work.');
    await user.click(screen.getByRole('button', { name: 'Create Cookbook' }));

    await waitFor(() => expect(createCookbookSpy).toHaveBeenCalledWith({
      name: 'Mexican',
      description: 'Regional dishes and masa work.',
    }));
    expect(screen.getByRole('heading', { name: 'Mexican' })).toBeInTheDocument();

    const moveSelect = screen.getByLabelText('Move to cookbook');
    await user.selectOptions(moveSelect, 'cookbook-mexican');
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not move that recipe just now.');

    await user.selectOptions(screen.getByLabelText('Move to cookbook'), 'cookbook-mexican');
    await waitFor(() => expect(moveSpy).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Every saved draft is currently tucked into a cookbook folder.')).toBeInTheDocument();
    expect(screen.getByText('Marinated peppers')).toBeInTheDocument();
  });

  it('creates and runs an authored session before navigating to the session detail', async () => {
    const user = userEvent.setup();
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue({
      session_id: 'session-123',
      user_id: 'user-1',
      status: 'pending',
      concept_json: {
        free_text: 'Schedule authored recipe: Marinated peppers',
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: null,
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
    });
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderWithAuth();

    await user.click(await screen.findByRole('button', { name: 'Schedule from shelf' }));

    await waitFor(() => expect(createSessionSpy).toHaveBeenCalledTimes(1));
    const request = createSessionSpy.mock.calls[0]?.[0];
    expect(request).toEqual({
      concept_source: 'authored',
      free_text: 'Schedule authored recipe: Marinated peppers',
      selected_authored_recipe: {
        recipe_id: 'recipe-2',
        title: 'Marinated peppers',
      },
      guest_count: 4,
      meal_type: 'dinner',
      occasion: 'dinner_party',
    });
    expect(request).not.toHaveProperty('planner_authored_recipe_anchor');
    expect(request).not.toHaveProperty('planner_cookbook_target');
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
    expect(mockNavigate).toHaveBeenCalledWith('/sessions/session-123');
  });

  it('prevents duplicate schedule clicks while a request is already in flight', async () => {
    const user = userEvent.setup();
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);

    let resolveCreate!: (value: Awaited<ReturnType<typeof sessionsApi.createSession>>) => void;
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderWithAuth();

    const button = await screen.findByRole('button', { name: 'Schedule from shelf' });
    await user.click(button);
    expect(await screen.findByRole('button', { name: 'Starting schedule…' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Starting schedule…' }));

    expect(createSessionSpy).toHaveBeenCalledTimes(1);
    expect(runPipelineSpy).not.toHaveBeenCalled();

    resolveCreate({
      session_id: 'session-123',
      user_id: 'user-1',
      status: 'pending',
      concept_json: {
        free_text: 'Schedule authored recipe: Marinated peppers',
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: null,
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
    });

    await waitFor(() => expect(runPipelineSpy).toHaveBeenCalledWith('session-123'));
  });

  it('shows inline recovery when session creation fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockRejectedValue(new Error('Session create failed.'));
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderWithAuth();

    await user.click(await screen.findByRole('button', { name: 'Schedule from shelf' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Session create failed.');
    expect(runPipelineSpy).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(createSessionSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Schedule from shelf' })).toBeEnabled();
  });

  it('shows inline recovery when the run kickoff fails after session creation', async () => {
    const user = userEvent.setup();
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);
    vi.spyOn(sessionsApi, 'createSession').mockResolvedValue({
      session_id: 'session-123',
      user_id: 'user-1',
      status: 'pending',
      concept_json: {
        free_text: 'Schedule authored recipe: Marinated peppers',
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: null,
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
    });
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockRejectedValue(new Error('Run kickoff failed.'));

    renderWithAuth();

    await user.click(await screen.findByRole('button', { name: 'Schedule from shelf' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Run kickoff failed.');
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Schedule from shelf' })).toBeEnabled();
  });

  it('does not send a schedule request when the selected row is missing recipe details', async () => {
    const user = userEvent.setup();
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: '',
        user_id: 'user-1',
        title: '',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue({
      session_id: 'session-123',
      user_id: 'user-1',
      status: 'pending',
      concept_json: {
        free_text: 'ignored',
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: null,
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
    });
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderWithAuth();

    await user.click(await screen.findByRole('button', { name: 'Schedule from shelf' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This saved draft is missing its scheduling details. Reopen it in the authoring workspace before trying again.',
    );
    expect(createSessionSpy).not.toHaveBeenCalled();
    expect(runPipelineSpy).not.toHaveBeenCalled();
  });

  it('preserves the initial library fetch failure surface', async () => {
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockRejectedValue(new Error('Could not load your recipe library.'));
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);

    renderWithAuth();

    expect(await screen.findByRole('heading', { name: 'The shelf did not load.' })).toBeInTheDocument();
    expect(screen.getByText('Could not load your recipe library.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Schedule from shelf' })).not.toBeInTheDocument();
  });
});
