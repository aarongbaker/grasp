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

const plannerAuthoredAnchorSession: Session = {
  ...menuSession,
  session_id: 'session-planner-authored-anchor',
  concept_json: {
    free_text: 'Use the chicken ballotine as the anchor and build a dinner around it',
    guest_count: 8,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: '19:30',
    concept_source: 'planner_authored_anchor',
    selected_recipes: [],
    selected_authored_recipe: null,
    planner_authored_recipe_anchor: {
      recipe_id: 'recipe-authored-1',
      title: 'Chicken Ballotine with Tarragon Jus',
    },
    planner_cookbook_target: null,
  },
};

const plannerCookbookTargetSession: Session = {
  ...menuSession,
  session_id: 'session-planner-cookbook-target',
  concept_json: {
    free_text: 'Build a dinner from the spring pastry folder',
    guest_count: 8,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: '19:30',
    concept_source: 'planner_cookbook_target',
    selected_recipes: [],
    selected_authored_recipe: null,
    planner_authored_recipe_anchor: null,
    planner_cookbook_target: {
      cookbook_id: 'cookbook-spring-pastry',
      name: 'Spring Pastry',
      description: 'Tarts, galettes, and plated fruit desserts.',
      mode: 'cookbook_biased',
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

  it('builds generated-planner display metadata from menu intent', () => {
    expect(getSessionConceptDisplay(menuSession.concept_json)).toEqual({
      title: 'A rustic Italian dinner with handmade pasta and seasonal vegetables',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Generated plan',
      sourceDetail: 'Built from a fresh dinner brief inside the dinner planner.',
    });
  });

  it('keeps free-text sessions on the original meal-idea presentation path', () => {
    expect(getSessionConceptDisplay(freeTextSession.concept_json)).toEqual({
      title: 'A bright spring dinner party with fish and citrus',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Generated plan',
      sourceDetail: 'Built from a fresh dinner brief inside the dinner planner.',
    });
  });

  it('prefers the authored recipe title and library labeling for authored sessions', () => {
    expect(getSessionConceptDisplay(authoredSession.concept_json)).toEqual({
      title: 'Chicken Ballotine with Tarragon Jus',
      pathwayKey: 'recipe-library',
      pathwayLabel: 'Browse Recipe Library',
      sourceLabel: 'Authored recipe',
      sourceDetail: 'Built from your private library so the session reflects a saved dish rather than a new menu brief.',
    });
  });

  it('keeps planner-authored anchors on the planner lane instead of mislabeling them as direct library sessions', () => {
    expect(getSessionConceptDisplay(plannerAuthoredAnchorSession.concept_json)).toEqual({
      title: 'Chicken Ballotine with Tarragon Jus',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Planner recipe anchor',
      sourceDetail: 'Built from the dinner planner using one saved recipe as the anchor for a broader service plan.',
    });
  });

  it('uses the cookbook folder name for planner cookbook targets', () => {
    expect(getSessionConceptDisplay(plannerCookbookTargetSession.concept_json)).toEqual({
      title: 'Spring Pastry',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Planner cookbook target',
      sourceDetail: 'Built from the dinner planner using one cookbook folder as the planning target.',
    });
  });

  it('ignores planner cookbook mode metadata when building shared display copy', () => {
    const strictPlannerCookbookTarget: DinnerConcept = {
      ...plannerCookbookTargetSession.concept_json,
      planner_cookbook_target: {
        cookbook_id: 'cookbook-spring-pastry',
        name: 'Spring Pastry',
        description: 'Tarts, galettes, and plated fruit desserts.',
        mode: 'strict',
      },
    };

    expect(getSessionConceptDisplay(strictPlannerCookbookTarget)).toEqual(
      getSessionConceptDisplay(plannerCookbookTargetSession.concept_json),
    );
  });

  it('falls back to free text when a planner-authored anchor is missing the trusted title', () => {
    const malformedConcept: DinnerConcept = {
      ...plannerAuthoredAnchorSession.concept_json,
      free_text: 'Fallback planner note',
      planner_authored_recipe_anchor: {
        recipe_id: 'recipe-authored-1',
        title: '   ',
      },
    };

    expect(getSessionConceptDisplay(malformedConcept)).toEqual({
      title: 'Fallback planner note',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Planner recipe anchor',
      sourceDetail: 'Built from the dinner planner with an authored anchor, but the saved recipe title was missing from the persisted concept.',
    });
  });

  it('falls back to free text when a planner cookbook target is missing the saved folder name', () => {
    const malformedConcept: DinnerConcept = {
      ...plannerCookbookTargetSession.concept_json,
      free_text: 'Fallback cookbook note',
      planner_cookbook_target: {
        cookbook_id: 'cookbook-spring-pastry',
        name: '   ',
        description: 'Tarts, galettes, and plated fruit desserts.',
        mode: 'cookbook_biased',
      },
    };

    expect(getSessionConceptDisplay(malformedConcept)).toEqual({
      title: 'Fallback cookbook note',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Planner cookbook target',
      sourceDetail: 'Built from the dinner planner with a cookbook target, but the saved folder name was missing from the persisted concept.',
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
      pathwayKey: 'recipe-library',
      pathwayLabel: 'Browse Recipe Library',
      sourceLabel: 'Authored recipe',
      sourceDetail: 'Built from the authored-recipe path. The saved title was missing, so the planning note is shown instead.',
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
      pathwayKey: 'recipe-library',
      pathwayLabel: 'Browse Recipe Library',
      sourceLabel: 'Authored recipe',
      sourceDetail: 'Built from the authored-recipe path. The saved title was missing, so the planning note is shown instead.',
    });
  });

  it('renders generated-planner labels on dashboard cards', () => {
    render(
      <MemoryRouter>
        <SessionCard session={menuSession} />
      </MemoryRouter>,
    );

    expect(screen.getByText('Generated plan')).toBeInTheDocument();
    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
    expect(screen.getByText('Plan a Dinner · Built from a fresh dinner brief inside the dinner planner.')).toBeInTheDocument();
  });

  it('renders authored labels on dashboard cards', () => {
    render(
      <MemoryRouter>
        <SessionCard session={authoredSession} />
      </MemoryRouter>,
    );

    expect(screen.getByText('Authored recipe')).toBeInTheDocument();
    expect(screen.getByText('Chicken Ballotine with Tarragon Jus')).toBeInTheDocument();
    expect(screen.getByText('Browse Recipe Library · Built from your private library so the session reflects a saved dish rather than a new menu brief.')).toBeInTheDocument();
  });

  it('renders shared generated-planner metadata on the session detail page without changing tabs or status flow', async () => {
    renderDetailPage();

    expect(screen.getByText('Generated plan')).toBeInTheDocument();
    expect(screen.getByText('Plan a Dinner')).toBeInTheDocument();
    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
    expect(screen.getByText('Built from a fresh dinner brief inside the dinner planner.')).toBeInTheDocument();
    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(menuSession.session_id));
  });

  it('renders shared authored metadata on the session detail page', async () => {
    vi.spyOn(sessionStatusHook, 'useSessionStatus').mockReturnValue({
      data: authoredSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });

    renderDetailPage(authoredSession.session_id);

    expect(screen.getByText('Authored recipe')).toBeInTheDocument();
    expect(screen.getByText('Browse Recipe Library')).toBeInTheDocument();
    expect(screen.getByText('Chicken Ballotine with Tarragon Jus')).toBeInTheDocument();
    expect(screen.getByText('Built from your private library so the session reflects a saved dish rather than a new menu brief.')).toBeInTheDocument();
    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(authoredSession.session_id));
  });

  it('keeps the existing detail retry banner when result fetching fails while header metadata stays stable', async () => {
    vi.spyOn(sessionsApi, 'getSessionResults').mockRejectedValue(new Error('Results unavailable'));

    renderDetailPage();

    expect(screen.getByText('Generated plan')).toBeInTheDocument();
    expect(screen.getByText('Plan a Dinner')).toBeInTheDocument();
    expect(await screen.findByText('Could not load results')).toBeInTheDocument();
    expect(screen.getByText('Results unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('uses generated-planner metadata in the PDF surface', () => {
    render(<RecipePDF session={menuSession} results={results} />);

    expect(screen.getByText('Generated plan')).toBeInTheDocument();
    expect(screen.getByText('Plan a Dinner')).toBeInTheDocument();
    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
    expect(screen.getByText('Built from a fresh dinner brief inside the dinner planner.')).toBeInTheDocument();
  });

  it('uses authored metadata in the PDF surface', () => {
    render(<RecipePDF session={authoredSession} results={results} />);

    expect(screen.getByText('Authored recipe')).toBeInTheDocument();
    expect(screen.getByText('Browse Recipe Library')).toBeInTheDocument();
    expect(screen.getByText('Chicken Ballotine with Tarragon Jus')).toBeInTheDocument();
    expect(screen.getByText('Built from your private library so the session reflects a saved dish rather than a new menu brief.')).toBeInTheDocument();
  });
});
