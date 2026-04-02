import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewSessionPage } from '../NewSessionPage';
import * as sessionsApi from '../../api/sessions';
import type { PlannerReferenceResolutionResponse, Session } from '../../types/api';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

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

function resolvedAuthoredResponse(): PlannerReferenceResolutionResponse {
  return {
    kind: 'authored',
    reference: 'sunday braise',
    status: 'resolved',
    matches: [
      {
        kind: 'authored',
        recipe_id: 'recipe-1',
        title: 'Sunday Braise',
      },
    ],
  };
}

function ambiguousCookbookResponse(): PlannerReferenceResolutionResponse {
  return {
    kind: 'cookbook',
    reference: 'desserts',
    status: 'ambiguous',
    matches: [
      {
        kind: 'cookbook',
        cookbook_id: 'cookbook-1',
        name: 'Desserts',
        description: 'Plated desserts.',
      },
      {
        kind: 'cookbook',
        cookbook_id: 'cookbook-2',
        name: 'Frozen Desserts',
        description: 'Ice cream service.',
      },
    ],
  };
}

function noMatchCookbookResponse(): PlannerReferenceResolutionResponse {
  return {
    kind: 'cookbook',
    reference: 'vegetables',
    status: 'no_match',
    matches: [],
  };
}

describe('NewSessionPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the planner lane with inline reference controls and guidance links', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Plan a Dinner' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Start here when service timing leads.' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Browse Recipe Library/i })).toHaveAttribute('href', '/recipes');
    expect(screen.getByRole('link', { name: /Start a Recipe Draft/i })).toHaveAttribute('href', '/recipes/new');
    expect(screen.getByLabelText('Planner anchor')).toBeInTheDocument();
    expect(
      screen.getByText(/No owned reference is required unless you want the planner anchored/i),
    ).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'authored');

    expect(screen.getByLabelText('Saved recipe reference')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resolve' })).toBeInTheDocument();
    expect(screen.getByText(/Resolve one owned recipe title inline so no-match, ambiguity, and retry states stay visible/i)).toBeInTheDocument();
  });

  it('submits a free-text planner request and navigates to the session detail page', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

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

  it('shows authored no-match recovery copy inline and keeps planner creation blocked', async () => {
    vi.spyOn(sessionsApi, 'resolvePlannerReference').mockResolvedValue({
      kind: 'authored',
      reference: 'braise notes',
      status: 'no_match',
      matches: [],
    });
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Build service around an existing braise');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'authored');
    await userEvent.type(screen.getByLabelText('Saved recipe reference'), 'Braise Notes');
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText(/Nothing in your saved recipes matched “braise notes”/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Correct the recipe title and resolve again\. The planner stays in this lane, but it will not start until one owned reference resolves\./i),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    expect(await screen.findByText('Resolve the saved recipe reference before starting the plan.')).toBeInTheDocument();
    expect(createSessionSpy).not.toHaveBeenCalled();
  });

  it('shows no-match cookbook results inline and blocks submit until a real resolution exists', async () => {
    vi.spyOn(sessionsApi, 'resolvePlannerReference').mockResolvedValue(noMatchCookbookResponse());
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A dessert service');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'cookbook');
    await userEvent.type(screen.getByLabelText('Cookbook reference'), 'Vegetables');
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText(/Nothing in your cookbooks matched “vegetables”/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Correct the cookbook name and resolve again\. The planner stays in this lane, but it will not start until one owned reference resolves\./i),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    expect(await screen.findByText('Resolve the cookbook reference before starting the plan.')).toBeInTheDocument();
    expect(createSessionSpy).not.toHaveBeenCalled();
  });

  it('blocks ambiguous cookbook matches until an explicit choice and mode are selected, then posts canonical ids', async () => {
    vi.spyOn(sessionsApi, 'resolvePlannerReference').mockResolvedValue(ambiguousCookbookResponse());
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A plated dessert tasting');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'cookbook');
    await userEvent.type(screen.getByLabelText('Cookbook reference'), 'Desserts');
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText(/Choose the exact cookbook before starting the planner/i)).toBeInTheDocument();
    expect(
      screen.getByText(/The planner stays blocked in this lane until you choose one exact match\./i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Review the owned matches below, choose the one you mean, then continue with this dinner brief\./i),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));
    expect(await screen.findByText('Choose the intended cookbook before starting the plan.')).toBeInTheDocument();
    expect(createSessionSpy).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('radio', { name: /^DessertsPlated desserts\.$/i }));
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));
    expect(
      await screen.findByText('Choose how tightly the planner should follow that cookbook before starting the plan.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/The planner remains blocked until you pick one mode, so the target cookbook guidance is explicit before session creation\./i),
    ).toBeInTheDocument();
    expect(createSessionSpy).not.toHaveBeenCalled();

    await userEvent.selectOptions(screen.getByLabelText('Cookbook planning mode'), 'cookbook_biased');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith({
        concept_source: 'planner_cookbook_target',
        free_text: 'A plated dessert tasting',
        planner_cookbook_target: {
          cookbook_id: 'cookbook-1',
          name: 'Desserts',
          mode: 'cookbook_biased',
        },
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: undefined,
      }),
    );
  });

  it('submits a resolved authored anchor with the canonical owned recipe match', async () => {
    vi.spyOn(sessionsApi, 'resolvePlannerReference').mockResolvedValue(resolvedAuthoredResponse());
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Build service around a braise');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'authored');
    await userEvent.type(screen.getByLabelText('Saved recipe reference'), 'Sunday Braise');
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText('Sunday Braise')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith({
        concept_source: 'planner_authored_anchor',
        free_text: 'Build service around a braise',
        planner_authored_recipe_anchor: {
          recipe_id: 'recipe-1',
          title: 'Sunday Braise',
        },
        guest_count: 4,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: [],
        serving_time: undefined,
      }),
    );
  });

  it('surfaces planner resolution API failures inline while preserving the form', async () => {
    vi.spyOn(sessionsApi, 'resolvePlannerReference').mockRejectedValue(new Error('Resolution unavailable'));
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A dessert service');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'cookbook');
    await userEvent.type(screen.getByLabelText('Cookbook reference'), 'Desserts');
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText('The planner could not confirm that owned reference right now.')).toBeInTheDocument();
    expect(screen.getByText('Resolution unavailable')).toBeInTheDocument();
    expect(
      screen.getByText(/Keep the dinner brief here, adjust the reference if needed, and resolve again when the library is reachable\./i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Cookbook reference')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));
    expect(await screen.findByText('Resolve the cookbook reference before starting the plan.')).toBeInTheDocument();
    expect(createSessionSpy).not.toHaveBeenCalled();
  });

  it('adds and removes dietary restrictions', async () => {
    renderPage();

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

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A bright spring dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    expect(await screen.findByText('Something went wrong — please try again')).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('navigates back to dashboard when cancel is clicked', async () => {
    renderPage();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(navigateMock).toHaveBeenCalledWith('/');
  });
});
