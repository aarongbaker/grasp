import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewSessionPage } from '../NewSessionPage';
import * as authoredRecipesApi from '../../api/authoredRecipes';
import * as recipeCookbooksApi from '../../api/recipeCookbooks';
import * as sessionsApi from '../../api/sessions';
import type { AuthoredRecipeListItem, RecipeCookbookDetail, Session } from '../../types/api';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

const plannerRecipes: AuthoredRecipeListItem[] = [
  {
    recipe_id: 'recipe-1',
    user_id: 'user-1',
    title: 'Braised fennel with citrus glaze',
    cuisine: 'Mediterranean',
    cookbook_id: null,
    cookbook: null,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-02T00:00:00Z',
  },
  {
    recipe_id: 'recipe-2',
    user_id: 'user-1',
    title: 'Marinated peppers',
    cuisine: 'Spanish',
    cookbook_id: 'cookbook-1',
    cookbook: {
      cookbook_id: 'cookbook-1',
      name: 'Dinner Party Staples',
      description: 'Reliable service anchors.',
    },
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-03T00:00:00Z',
  },
];

const plannerCookbooks: RecipeCookbookDetail[] = [
  {
    cookbook_id: 'cookbook-1',
    user_id: 'user-1',
    name: 'Dinner Party Staples',
    description: 'Reliable service anchors.',
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-02T00:00:00Z',
  },
  {
    cookbook_id: 'cookbook-2',
    user_id: 'user-1',
    name: 'Late Summer Menu',
    description: 'Stone fruit and charcoal notes.',
    created_at: '2026-04-03T00:00:00Z',
    updated_at: '2026-04-04T00:00:00Z',
  },
];

const createdSession: Session = {
  session_id: 'session-123',
  user_id: 'user-1',
  status: 'pending',
  concept_json: {
    free_text: 'A bright spring dinner',
    guest_count: 4,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: null,
    concept_source: 'free_text',
    selected_recipes: [],
    selected_authored_recipe: null,
    planner_authored_recipe_anchor: null,
    planner_cookbook_target: null,
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

function renderPage() {
  return render(
    <MemoryRouter>
      <NewSessionPage />
    </MemoryRouter>,
  );
}

describe('NewSessionPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.restoreAllMocks();
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue(plannerRecipes);
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue(plannerCookbooks);
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the planner lane with anchor controls and guidance links', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Plan a Dinner' })).toBeInTheDocument();
    expect(screen.getByText(/Describe the meal you want to cook/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Start here when service timing leads.' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Browse Recipe Library/i })).toHaveAttribute('href', '/recipes');
    expect(screen.getByRole('link', { name: /Start a Recipe Draft/i })).toHaveAttribute('href', '/recipes/new');
    expect(screen.getByLabelText('Planner anchor')).toBeInTheDocument();
    expect(screen.getByText(/Loading owned planner references/i)).toBeInTheDocument();

    expect(await screen.findByText(/2 saved recipes and 2 cookbooks ready/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Saved recipe anchor')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Cookbook target')).not.toBeInTheDocument();
  });

  it('submits a free-text planner request and navigates to the session detail page', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A bright spring dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith({
        concept_source: 'free_text',
        free_text: 'A bright spring dinner',
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: undefined,
      }),
    );
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
  });

  it('submits a planner authored anchor request with the selected saved recipe', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Build a service around the fennel course');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'authored');
    await userEvent.selectOptions(screen.getByLabelText('Saved recipe anchor'), 'recipe-1');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith({
        concept_source: 'planner_authored_anchor',
        free_text: 'Build a service around the fennel course',
        planner_authored_recipe_anchor: {
          recipe_id: 'recipe-1',
          title: 'Braised fennel with citrus glaze',
        },
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: undefined,
      }),
    );
  });

  it('submits a planner cookbook target request with the selected shelf', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Shape a late-summer dinner for six');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'cookbook');
    await userEvent.selectOptions(screen.getByLabelText('Cookbook target'), 'cookbook-2');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith({
        concept_source: 'planner_cookbook_target',
        free_text: 'Shape a late-summer dinner for six',
        planner_cookbook_target: {
          cookbook_id: 'cookbook-2',
          name: 'Late Summer Menu',
        },
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: undefined,
      }),
    );
  });

  it('surfaces create-time planner anchor validation and does not kick off the run', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline');

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Build a service around the fennel course');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'authored');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    expect(await screen.findByText('Choose one saved recipe anchor before starting the plan.')).toBeInTheDocument();
    expect(createSessionSpy).not.toHaveBeenCalled();
    expect(runPipelineSpy).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('keeps the loading state until a create failure settles and does not kick off the run', async () => {
    let rejectCreate!: (reason?: unknown) => void;
    vi.spyOn(sessionsApi, 'createSession').mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectCreate = reject;
        }),
    );
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline');

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A bright spring dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    expect(await screen.findByRole('button', { name: 'Starting...' })).toBeDisabled();
    expect(runPipelineSpy).not.toHaveBeenCalled();

    rejectCreate(new Error('Session creation failed'));

    expect(await screen.findByText('Session creation failed')).toBeInTheDocument();
    expect(runPipelineSpy).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Start Planning' })).toBeEnabled();
  });

  it('keeps the loading state until the run kickoff settles and only navigates after create then run', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    let resolveRun!: (value: Awaited<ReturnType<typeof sessionsApi.runPipeline>>) => void;
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRun = resolve;
        }),
    );

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A bright spring dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    expect(await screen.findByRole('button', { name: 'Starting...' })).toBeDisabled();
    expect(createSessionSpy).toHaveBeenCalledTimes(1);
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
    expect(navigateMock).not.toHaveBeenCalled();

    resolveRun({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123'));
  });

  it('displays fallback error text when the run kickoff rejects with a malformed response', async () => {
    vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockRejectedValue({ unexpected: true });

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A bright spring dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    expect(await screen.findByText('Something went wrong — please try again')).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('shows planner library load failures inline while preserving the planner form', async () => {
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockRejectedValue(new Error('Library unavailable'));

    renderPage();

    expect(await screen.findByText('Library unavailable')).toBeInTheDocument();
    expect(screen.getByLabelText('Planner anchor')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Planning' })).toBeInTheDocument();
  });

  it('adds and removes dietary restrictions', async () => {
    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);

    const restrictionInput = screen.getByLabelText('Dietary restrictions');
    await userEvent.type(restrictionInput, 'gluten-free{Enter}');

    expect(screen.getByText('gluten-free')).toBeInTheDocument();
    expect(restrictionInput).toHaveValue('');

    await userEvent.type(restrictionInput, 'dairy-free{Enter}');
    expect(screen.getByText('dairy-free')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Remove gluten-free' }));
    expect(screen.queryByText('gluten-free')).not.toBeInTheDocument();
    expect(screen.getByText('dairy-free')).toBeInTheDocument();
  });

  it('keeps the submit disabled for blank menu intent text', async () => {
    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);

    const submitButton = screen.getByRole('button', { name: 'Start Planning' });
    expect(submitButton).toBeDisabled();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Dinner');
    expect(submitButton).not.toBeDisabled();

    await userEvent.clear(screen.getByLabelText('What are you cooking?'));
    expect(submitButton).toBeDisabled();
  });

  it('allows customizing guest count, meal type, and serving time', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A festive brunch');
    await userEvent.clear(screen.getByLabelText('Guests'));
    await userEvent.type(screen.getByLabelText('Guests'), '8');
    await userEvent.selectOptions(screen.getByLabelText('Meal type'), 'lunch');
    await userEvent.type(screen.getByLabelText('Serving time'), '12:30');

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith({
        concept_source: 'free_text',
        free_text: 'A festive brunch',
        guest_count: 8,
        meal_type: 'lunch',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: '12:30',
      }),
    );
  });

  it('navigates back to dashboard when cancel is clicked', async () => {
    renderPage();

    await screen.findByText(/2 saved recipes and 2 cookbooks ready/i);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(navigateMock).toHaveBeenCalledWith('/');
  });
});
