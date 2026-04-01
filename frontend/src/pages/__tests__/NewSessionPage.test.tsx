import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewSessionPage } from '../NewSessionPage';
import * as sessionsApi from '../../api/sessions';
import type { Session } from '../../types/api';

const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <NewSessionPage />
    </MemoryRouter>,
  );
}

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

describe('NewSessionPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders only menu intent form with route-local guidance and no cookbook mode switcher', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Plan a Dinner' })).toBeInTheDocument();
    expect(screen.getByText(/Describe the meal you want to cook/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Start here when service timing leads.' })).toBeInTheDocument();
    expect(screen.getByText(/Keep this route for menu-intent planning/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Browse Recipe Library/i })).toHaveAttribute('href', '/recipes');
    expect(screen.getByRole('link', { name: /Start a Recipe Draft/i })).toHaveAttribute('href', '/recipes/new');

    expect(screen.queryByText(/Select from cookbook/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/cookbook mode/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/free-text mode/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /switch.*mode/i })).not.toBeInTheDocument();
  });

  it('renders the menu intent form with all required fields', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Plan a Dinner' })).toBeInTheDocument();
    expect(screen.getByText(/Describe the meal you want to cook/i)).toBeInTheDocument();
    expect(screen.getByLabelText('What are you cooking?')).toBeInTheDocument();
    expect(screen.getByLabelText('Guests')).toBeInTheDocument();
    expect(screen.getByLabelText('Meal type')).toBeInTheDocument();
    expect(screen.getByLabelText('Occasion')).toBeInTheDocument();
    expect(screen.getByLabelText('Dietary restrictions')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Planning' })).toBeInTheDocument();
  });

  it('submits the menu intent and navigates to the session detail page', async () => {
    const callOrder: string[] = [];
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession').mockImplementation(async (payload) => {
      callOrder.push(`create:${JSON.stringify(payload)}`);
      return createdSession;
    });
    const runPipelineSpy = vi.spyOn(sessionsApi, 'runPipeline').mockImplementation(async (sessionId) => {
      callOrder.push(`run:${sessionId}`);
      return {
        session_id: 'session-123',
        status: 'generating',
        message: 'Pipeline enqueued',
      };
    });

    renderPage();

    await userEvent.type(screen.getByLabelText('What are you cooking?'), 'A bright spring dinner');
    await userEvent.click(screen.getByRole('button', { name: 'Start Planning' }));

    await waitFor(() => expect(createSessionSpy).toHaveBeenCalledTimes(1));
    expect(createSessionSpy).toHaveBeenCalledWith({
      free_text: 'A bright spring dinner',
      guest_count: 4,
      meal_type: 'dinner',
      occasion: 'dinner_party',
      dietary_restrictions: [],
      serving_time: undefined,
    });
    expect(runPipelineSpy).toHaveBeenCalledWith('session-123');
    expect(callOrder).toEqual([
      'create:{"free_text":"A bright spring dinner","guest_count":4,"meal_type":"dinner","occasion":"dinner_party","dietary_restrictions":[]}',
      'run:session-123',
    ]);
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
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

    await waitFor(() => expect(createSessionSpy).toHaveBeenCalledWith({
      free_text: 'A festive brunch',
      guest_count: 8,
      meal_type: 'lunch',
      occasion: 'dinner_party',
      dietary_restrictions: [],
      serving_time: '12:30',
    }));
  });

  it('navigates back to dashboard when cancel is clicked', async () => {
    renderPage();

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(navigateMock).toHaveBeenCalledWith('/');
  });
});
