import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    dish_count: 3,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: null,
    concept_source: 'free_text',
    selected_recipes: [],
    selected_authored_recipe: null,
    planner_authored_recipe_anchor: null,
    planner_cookbook_target: null,
    planner_catalog_cookbook: null,
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

  it('hydrates an included catalog handoff and submits the catalog-backed session lane', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/',
            state: {
              plannerCatalogCookbook: {
                catalog_cookbook_id: 'catalog-1',
                slug: 'weeknight-foundations',
                title: 'Weeknight Foundations',
                access_state: 'included',
                access_state_reason: 'Included with your current catalog access.',
                access_diagnostics: {
                  subscription_snapshot_id: 'snapshot-1',
                  subscription_status: 'active',
                  sync_state: 'synced',
                  provider: 'stripe',
                },
              },
            },
          },
        ]}
      >
        <NewSessionPage />
      </MemoryRouter>,
    );

    expect(screen.getByText('Catalog cookbook included')).toBeInTheDocument();
    expect(screen.getByText('Weeknight Foundations')).toBeInTheDocument();
    expect(screen.queryByLabelText('Planner anchor')).not.toBeInTheDocument();
    expect(screen.queryByText(/pre-existing cookbook recipes/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/cookbook shelf/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/stripe|active|synced|snapshot/i)).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Build a weeknight dinner from the catalog lane');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          concept_source: 'planner_catalog_cookbook',
          free_text: 'Build a weeknight dinner from the catalog lane',
          planner_catalog_cookbook: {
            catalog_cookbook_id: 'catalog-1',
          },
        }),
      ),
    );
    expect(createSessionSpy.mock.calls[0]?.[0]).not.toHaveProperty('planner_cookbook_target');
    expect(createSessionSpy.mock.calls[0]?.[0].planner_catalog_cookbook).not.toHaveProperty('access_diagnostics');
  });

  it('shows preview catalog guidance but still allows planner submission through the catalog lane', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/',
            state: {
              plannerCatalogCookbook: {
                catalog_cookbook_id: 'catalog-2',
                slug: 'spring-pastry',
                title: 'Spring Pastry',
                access_state: 'preview',
                access_state_reason: 'Preview access is available for this catalog cookbook.',
              },
            },
          },
        ]}
      >
        <NewSessionPage />
      </MemoryRouter>,
    );

    expect(screen.getByText('Catalog cookbook preview')).toBeInTheDocument();
    expect(screen.getByText(/Preview access is valid for planning/i)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Seed dessert service from the preview catalog lane');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          concept_source: 'planner_catalog_cookbook',
          planner_catalog_cookbook: {
            catalog_cookbook_id: 'catalog-2',
          },
        }),
      ),
    );
  });

  it('blocks locked catalog handoffs inline before session creation', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/',
            state: {
              plannerCatalogCookbook: {
                catalog_cookbook_id: 'catalog-3',
                slug: 'premium-desserts',
                title: 'Premium Desserts',
                access_state: 'locked',
                access_state_reason: 'Upgrade access is required before this cookbook can be used in planning.',
                access_diagnostics: {
                  subscription_snapshot_id: 'snapshot-3',
                  subscription_status: 'past_due',
                  sync_state: 'stale',
                  provider: 'stripe',
                },
              },
            },
          },
        ]}
      >
        <NewSessionPage />
      </MemoryRouter>,
    );

    const submitButton = screen.getByRole('button', { name: 'Start Planning' });
    expect(screen.getByText('Catalog cookbook locked')).toBeInTheDocument();
    expect(screen.getByText(/session creation stays disabled here/i)).toBeInTheDocument();
    expect(submitButton).toBeDisabled();
    expect(createSessionSpy).not.toHaveBeenCalled();
  });

  it('surfaces malformed catalog handoffs inline before session creation', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');

    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/',
            state: {
              plannerCatalogCookbook: {
                catalog_cookbook_id: 'catalog-4',
                title: 'Broken Handoff',
              },
            },
          },
        ]}
      >
        <NewSessionPage />
      </MemoryRouter>,
    );

    const submitButton = screen.getByRole('button', { name: 'Start Planning' });
    expect(screen.getByText('Catalog handoff invalid')).toBeInTheDocument();
    expect(screen.getByText(/The handoff shape was malformed/i)).toBeInTheDocument();
    expect(submitButton).toBeDisabled();
    expect(createSessionSpy).not.toHaveBeenCalled();
  });

  it('renders the planner lane with inline reference controls and guidance links', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Plan a Dinner' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Start here when service timing leads.' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Browse Cookbook Catalog/i })).toHaveAttribute('href', '/catalog');
    expect(screen.getByRole('link', { name: /Browse Recipe Library/i })).toHaveAttribute('href', '/recipes');
    expect(screen.getByRole('link', { name: /Start a Recipe Draft/i })).toHaveAttribute('href', '/recipes/new');
    expect(
      screen.getByText(/Keep this route for menu-intent planning\. It stays focused on a single dinner brief and does not switch into/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/a platform catalog cookbook handoff, or one owned recipe or cookbook folder\./i)).toBeInTheDocument();
    expect(screen.getByLabelText('Dishes')).toHaveValue(3);
    expect(
      screen.getByText(/No owned reference is required unless you want the planner anchored/i),
    ).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'authored');

    expect(screen.getByLabelText('Saved recipe reference')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resolve' })).toBeInTheDocument();
    expect(screen.getByText(/Resolve one owned recipe title inline so no-match, ambiguity, and retry states stay visible/i)).toBeInTheDocument();
  });

  it('keeps the literal free-text planner story on the plain planner payload lane', async () => {
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    fireEvent.change(screen.getByLabelText('Dishes'), { target: { value: '4' } });
    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Around my chicken piccata');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          concept_source: 'free_text',
          free_text: 'Around my chicken piccata',
          guest_count: 4,
          dish_count: 4,
          meal_type: 'dinner',
          occasion: 'dinner_party',
          dietary_restrictions: [],
          serving_time: undefined,
        }),
      ),
    );
    expect(createSessionSpy.mock.calls[0]?.[0]).not.toHaveProperty('planner_authored_recipe_anchor');
    expect(createSessionSpy.mock.calls[0]?.[0]).not.toHaveProperty('planner_cookbook_target');
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

  it('keeps the literal cookbook-target planner story on the cookbook-target payload lane', async () => {
    vi.spyOn(sessionsApi, 'resolvePlannerReference').mockResolvedValue({
      kind: 'cookbook',
      reference: 'vegetarian cookbook',
      status: 'resolved',
      matches: [
        {
          kind: 'cookbook',
          cookbook_id: 'cookbook-vegetarian',
          name: 'Vegetarian Cookbook',
          description: 'Lunches, mains, and sides built from vegetables.',
        },
      ],
    });
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Vegetarian lunch from my vegetarian cookbook');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'cookbook');
    await userEvent.type(screen.getByLabelText('Cookbook reference'), 'Vegetarian Cookbook');
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText('Vegetarian Cookbook')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Cookbook planning mode'), 'cookbook_biased');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          concept_source: 'planner_cookbook_target',
          free_text: 'Vegetarian lunch from my vegetarian cookbook',
          planner_cookbook_target: {
            cookbook_id: 'cookbook-vegetarian',
            name: 'Vegetarian Cookbook',
            mode: 'cookbook_biased',
          },
          guest_count: 4,
          dish_count: 3,
          meal_type: 'dinner',
          occasion: 'dinner_party',
          dietary_restrictions: [],
          serving_time: undefined,
        }),
      ),
    );
    expect(createSessionSpy.mock.calls[0]?.[0]).not.toHaveProperty('selected_authored_recipe');
    expect(createSessionSpy.mock.calls[0]?.[0]).not.toHaveProperty('planner_authored_recipe_anchor');
  });

  it('keeps the literal authored-anchor planner story on the planner-authored payload lane', async () => {
    vi.spyOn(sessionsApi, 'resolvePlannerReference').mockResolvedValue({
      kind: 'authored',
      reference: 'chicken piccata',
      status: 'resolved',
      matches: [
        {
          kind: 'authored',
          recipe_id: 'recipe-piccata',
          title: 'Chicken Piccata',
        },
      ],
    });
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockResolvedValue(createdSession);
    vi.spyOn(sessionsApi, 'runPipeline').mockResolvedValue({
      session_id: 'session-123',
      status: 'generating',
      message: 'Pipeline enqueued',
    });

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'Around my chicken piccata');
    await userEvent.selectOptions(screen.getByLabelText('Planner anchor'), 'authored');
    await userEvent.type(screen.getByLabelText('Saved recipe reference'), 'Chicken Piccata');
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(await screen.findByText('Chicken Piccata')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          concept_source: 'planner_authored_anchor',
          free_text: 'Around my chicken piccata',
          planner_authored_recipe_anchor: {
            recipe_id: 'recipe-piccata',
            title: 'Chicken Piccata',
          },
          guest_count: 4,
          dish_count: 3,
          meal_type: 'dinner',
          occasion: 'dinner_party',
          dietary_restrictions: [],
          serving_time: undefined,
        }),
      ),
    );
    expect(createSessionSpy.mock.calls[0]?.[0]).not.toHaveProperty('selected_authored_recipe');
  });


  it('still blocks ambiguous cookbook matches before any planner-created cookbook session can post', async () => {
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
    fireEvent.change(screen.getByLabelText('Guests'), { target: { value: '8' } });
    await userEvent.selectOptions(screen.getByLabelText('Meal type'), 'lunch');
    await userEvent.type(screen.getByLabelText('Serving time'), '12:30');

    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() =>
      expect(createSessionSpy).toHaveBeenCalledWith({
        concept_source: 'free_text',
        free_text: 'A festive brunch',
        guest_count: 8,
        dish_count: 3,
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

