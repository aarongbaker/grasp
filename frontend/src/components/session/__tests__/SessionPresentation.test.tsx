import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionCard } from '../SessionCard';
import { RecipeCard } from '../RecipeCard';
import { RecipePDF } from '../RecipePDF';
import { ScheduleTimeline } from '../ScheduleTimeline';
import { getRecipeProvenanceDisplay, getSessionConceptDisplay } from '../sessionConceptDisplay';
import { SessionDetailPage } from '../../../pages/SessionDetailPage';
import type { DinnerConcept, NaturalLanguageSchedule, Session, SessionResults, ValidatedRecipe } from '../../../types/api';
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

const plannerLibraryRecipe: ValidatedRecipe = {
  source: {
    source: {
      name: 'Chicken Ballotine with Tarragon Jus',
      description: 'A composed plated main with sauce and garnish.',
      servings: 8,
      cuisine: 'French',
      estimated_total_minutes: 95,
      ingredients: [
        { name: 'Chicken', quantity: '1', preparation: 'deboned' },
        { name: 'Tarragon', quantity: '2 tbsp', preparation: 'chopped' },
      ],
      steps: ['Roll and poach the ballotine.'],
      provenance: {
        kind: 'library_authored',
        source_label: 'Chicken Ballotine with Tarragon Jus',
        recipe_id: 'recipe-authored-1',
        cookbook_id: null,
      },
    },
    steps: [
      {
        step_id: 'step-1',
        description: 'Roll, tie, and poach the chicken until just set.',
        duration_minutes: 30,
        duration_max: null,
        depends_on: [],
        resource: 'hands',
        required_equipment: [],
        can_be_done_ahead: false,
        prep_ahead_window: null,
        prep_ahead_notes: null,
      },
    ],
    rag_sources: ['legacy-library-tag'],
    chef_notes: 'Rest before slicing.',
    techniques_used: ['Poaching'],
  },
  validated_at: '2026-03-27T00:20:00Z',
  warnings: [],
  passed: true,
};

const plannerGeneratedRecipe: ValidatedRecipe = {
  source: {
    source: {
      name: 'Charred Leek Vinaigrette',
      description: 'A sharp, warm accompaniment for the main course.',
      servings: 8,
      cuisine: 'French',
      estimated_total_minutes: 20,
      ingredients: [
        { name: 'Leeks', quantity: '3', preparation: 'charred' },
      ],
      steps: ['Char and dress the leeks.'],
      provenance: {
        kind: 'generated',
        source_label: null,
        recipe_id: null,
        cookbook_id: null,
      },
    },
    steps: [
      {
        step_id: 'step-2',
        description: 'Char the leeks and finish with vinaigrette.',
        duration_minutes: 12,
        duration_max: null,
        depends_on: [],
        resource: 'stovetop',
        required_equipment: [],
        can_be_done_ahead: false,
        prep_ahead_window: null,
        prep_ahead_notes: null,
      },
    ],
    rag_sources: ['legacy-generated-noise'],
    chef_notes: '',
    techniques_used: [],
  },
  validated_at: '2026-03-27T00:20:00Z',
  warnings: [],
  passed: true,
};

const plannerCookbookRecipe: ValidatedRecipe = {
  source: {
    source: {
      name: 'Rhubarb Galette',
      description: 'A rustic dessert finished with vanilla cream.',
      servings: 8,
      cuisine: 'Seasonal pastry',
      estimated_total_minutes: 55,
      ingredients: [
        { name: 'Rhubarb', quantity: '500 g', preparation: 'trimmed' },
      ],
      steps: ['Bake the galette.'],
      provenance: {
        kind: 'library_cookbook',
        source_label: 'Spring Pastry',
        recipe_id: null,
        cookbook_id: 'cookbook-spring-pastry',
      },
    },
    steps: [
      {
        step_id: 'step-3',
        description: 'Bake until deeply golden and bubbling.',
        duration_minutes: 40,
        duration_max: null,
        depends_on: [],
        resource: 'oven',
        required_equipment: [],
        can_be_done_ahead: true,
        prep_ahead_window: '2 hours',
        prep_ahead_notes: 'Warm before serving.',
      },
    ],
    rag_sources: [],
    chef_notes: '',
    techniques_used: ['Baking'],
  },
  validated_at: '2026-03-27T00:20:00Z',
  warnings: [],
  passed: true,
};

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

const partialConflictSession: Session = {
  ...menuSession,
  session_id: 'session-partial-conflict',
  status: 'partial',
  error_summary: 'Oven temperature conflict: dessert must be staged after the braise window.',
};

const failedConflictSession: Session = {
  ...menuSession,
  session_id: 'session-failed-conflict',
  status: 'failed',
  error_summary: 'Oven temperature conflict: roast and dessert need incompatible temperatures at the same time.',
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
    planner_catalog_cookbook: null,
    planner_cookbook_target: {
      cookbook_id: 'cookbook-spring-pastry',
      name: 'Spring Pastry',
      description: 'Tarts, galettes, and plated fruit desserts.',
      mode: 'cookbook_biased',
    },
  },
};

const plannerCatalogCookbookSession: Session = {
  ...menuSession,
  session_id: 'session-planner-catalog-cookbook',
  concept_json: {
    free_text: 'Build a dinner from the platform weeknight catalog',
    guest_count: 6,
    meal_type: 'dinner',
    occasion: 'dinner_party',
    dietary_restrictions: [],
    serving_time: '18:30',
    concept_source: 'planner_catalog_cookbook',
    selected_recipes: [],
    selected_authored_recipe: null,
    planner_authored_recipe_anchor: null,
    planner_cookbook_target: null,
    planner_catalog_cookbook: {
      catalog_cookbook_id: 'catalog-1',
      slug: 'weeknight-foundations',
      title: 'Weeknight Foundations',
      access_state: 'included',
      access_state_reason: 'Included with your current catalog access.',
    },
  },
};

const baseSchedule: NaturalLanguageSchedule = {
  timeline: [],
  total_duration_minutes: 95,
  total_duration_minutes_max: null,
  active_time_minutes: 70,
  summary: 'Dinner lands all at once.',
  error_summary: null,
};

const results: SessionResults = {
  schedule: baseSchedule,
  recipes: [plannerLibraryRecipe, plannerGeneratedRecipe, plannerCookbookRecipe],
  errors: [],
};

const resequencedResults: SessionResults = {
  ...results,
  schedule: {
    ...baseSchedule,
    one_oven_conflict: {
      classification: 'resequence_required',
      tolerance_f: 15,
      has_second_oven: false,
      temperature_gap_f: 75,
      blocking_recipe_names: ['Braised Short Ribs', 'Chocolate Fondant'],
      affected_step_ids: ['ribs-bake', 'fondant-bake'],
      remediation: {
        requires_resequencing: true,
        suggested_actions: [
          'Bake Chocolate Fondant after Braised Short Ribs finishes.',
          'Prep the fondant batter earlier so it is ready when the oven frees up.',
        ],
        delaying_recipe_names: ['Chocolate Fondant'],
        blocking_recipe_names: ['Braised Short Ribs'],
        notes: 'Single-oven schedule remains feasible if dessert is staged after the braise window.',
      },
    },
  },
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
    expect(getSessionConceptDisplay(plannerCookbookTargetSession.concept_json).sourceDetail).not.toMatch(/catalog/i);
  });

  it('keeps catalog-backed planner sessions in a distinct catalog lane instead of masquerading as private cookbook folders', () => {
    expect(getSessionConceptDisplay(plannerCatalogCookbookSession.concept_json)).toEqual({
      title: 'Weeknight Foundations',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Planner catalog cookbook',
      sourceDetail: 'Built from the dinner planner using one platform catalog cookbook as the planning seed.',
    });
    expect(getSessionConceptDisplay(plannerCatalogCookbookSession.concept_json).sourceDetail).not.toMatch(/folder/i);
  });

  it('renders a persisted catalog-backed planner session with catalog wording instead of private-library wording', () => {
    render(
      <MemoryRouter>
        <SessionCard session={plannerCatalogCookbookSession} onDelete={vi.fn()} />
      </MemoryRouter>,
    );

    expect(screen.getByText('Weeknight Foundations')).toBeInTheDocument();
    expect(screen.getByText('Planner catalog cookbook')).toBeInTheDocument();
    expect(
      screen.getByText(/Plan a Dinner\s*·\s*Built from the dinner planner using one platform catalog cookbook as the planning seed\./i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Planner cookbook target/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/private library/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/cookbook folder/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/detected[- ]recipes?/i)).not.toBeInTheDocument();
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

  it('falls back to free text when a planner catalog cookbook is missing the trusted title', () => {
    const malformedConcept: DinnerConcept = {
      ...plannerCatalogCookbookSession.concept_json,
      free_text: 'Fallback catalog note',
      planner_catalog_cookbook: {
        catalog_cookbook_id: 'catalog-1',
        slug: 'weeknight-foundations',
        title: '   ',
        access_state: 'included',
        access_state_reason: 'Included with your current catalog access.',
      },
    };

    expect(getSessionConceptDisplay(malformedConcept)).toEqual({
      title: 'Fallback catalog note',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Planner catalog cookbook',
      sourceDetail: 'Built from the dinner planner with a catalog cookbook, but the trusted catalog title was missing from the persisted concept.',
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
  it('maps canonical recipe provenance without falling back to rag-source heuristics', () => {
    expect(getRecipeProvenanceDisplay(plannerLibraryRecipe.source.source.provenance)).toEqual({
      label: 'From your recipe library',
      detail: 'Anchored to your saved recipe “Chicken Ballotine with Tarragon Jus”.',
    });

    expect(getRecipeProvenanceDisplay(plannerGeneratedRecipe.source.source.provenance)).toEqual({
      label: 'Generated for this session',
      detail: 'Composed by the planner to complete this service.',
    });

    expect(getRecipeProvenanceDisplay(plannerCookbookRecipe.source.source.provenance)).toEqual({
      label: 'From your cookbook library',
      detail: 'Shelved in your cookbook folder “Spring Pastry”.',
    });
  });

  it('keeps planner session labeling separate from per-dish provenance in the assembled authored-anchor story', () => {
    expect(getSessionConceptDisplay(plannerAuthoredAnchorSession.concept_json)).toEqual({
      title: 'Chicken Ballotine with Tarragon Jus',
      pathwayKey: 'generated-planner',
      pathwayLabel: 'Plan a Dinner',
      sourceLabel: 'Planner recipe anchor',
      sourceDetail: 'Built from the dinner planner using one saved recipe as the anchor for a broader service plan.',
    });

    expect(getRecipeProvenanceDisplay(plannerLibraryRecipe.source.source.provenance)).toEqual({
      label: 'From your recipe library',
      detail: 'Anchored to your saved recipe “Chicken Ballotine with Tarragon Jus”.',
    });

    expect(getRecipeProvenanceDisplay(plannerGeneratedRecipe.source.source.provenance)).toEqual({
      label: 'Generated for this session',
      detail: 'Composed by the planner to complete this service.',
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

  it('renders canonical recipe provenance on cards instead of heuristic library tags', () => {
    render(<RecipeCard recipe={plannerLibraryRecipe} />);

    expect(screen.getByText('From your recipe library')).toBeInTheDocument();
    expect(screen.getByText('Anchored to your saved recipe “Chicken Ballotine with Tarragon Jus”.')).toBeInTheDocument();
    expect(screen.queryByText(/from library/i)).not.toBeInTheDocument();
  });

  it('renders cookbook provenance on cards from canonical provenance state', () => {
    render(<RecipeCard recipe={plannerCookbookRecipe} />);

    expect(screen.getByText('From your cookbook library')).toBeInTheDocument();
    expect(screen.getByText('Shelved in your cookbook folder “Spring Pastry”.')).toBeInTheDocument();
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

  it('renders canonical per-recipe provenance in the session detail recipes tab', async () => {
    renderDetailPage();

    const recipesTab = await screen.findByRole('button', { name: 'Recipes (3)' });
    await act(async () => {
      recipesTab.click();
    });

    expect(await screen.findByText('From your recipe library')).toBeInTheDocument();
    expect(screen.getByText('Anchored to your saved recipe “Chicken Ballotine with Tarragon Jus”.')).toBeInTheDocument();
    expect(screen.getByText('Generated for this session')).toBeInTheDocument();
    expect(screen.getByText('From your cookbook library')).toBeInTheDocument();
    expect(screen.queryByText(/recovered from the cookbook collection/i)).not.toBeInTheDocument();
  });

  it('renders structured one-oven guidance on the session detail page when results include resequencing metadata', async () => {
    vi.spyOn(sessionsApi, 'getSessionResults').mockResolvedValue(resequencedResults);

    renderDetailPage();

    expect(await screen.findByText('One-oven schedule needs staging, not a full replan')).toBeInTheDocument();
    expect(screen.getAllByText('Bake Chocolate Fondant after Braised Short Ribs finishes.')).toHaveLength(2);
    expect(screen.getAllByText('Temperature gap: 75°F').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Next step: Review the timeline and stage the later bake when the oven frees up.')).toBeInTheDocument();
    expect(screen.getByText('If service timing changes, regenerate with a second oven or a different bake mix.')).toBeInTheDocument();
    expect(screen.getByText('One-oven plan still works')).toBeInTheDocument();
    expect(screen.queryByText('One-oven conflict affected the original plan')).not.toBeInTheDocument();
  });

  it('keeps the session detail page quiet for compatible schedules and legacy schedules without one-oven metadata', async () => {
    renderDetailPage();

    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(menuSession.session_id));
    expect(screen.queryByText('One-oven schedule needs staging, not a full replan')).not.toBeInTheDocument();
    expect(screen.queryByText('One-oven plan still works')).not.toBeInTheDocument();

    cleanup();
    vi.restoreAllMocks();
    vi.spyOn(sessionStatusHook, 'useSessionStatus').mockReturnValue({
      data: menuSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });
    vi.spyOn(sessionsApi, 'getSessionResults').mockResolvedValue({
      ...results,
      schedule: {
        ...baseSchedule,
        timeline: [],
      },
    });

    renderDetailPage();

    await waitFor(() => expect(sessionsApi.getSessionResults).toHaveBeenCalledWith(menuSession.session_id));
    expect(screen.queryByText('One-oven schedule needs staging, not a full replan')).not.toBeInTheDocument();
    expect(screen.queryByText('One-oven plan still works')).not.toBeInTheDocument();
  });

  it('keeps the failure fallback banner for irreconcilable sessions when structured results are unavailable', () => {
    vi.spyOn(sessionStatusHook, 'useSessionStatus').mockReturnValue({
      data: failedConflictSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });

    renderDetailPage(failedConflictSession.session_id);

    expect(screen.getByText('Pipeline failed')).toBeInTheDocument();
    expect(screen.getByText('One-oven conflict blocked this menu')).toBeInTheDocument();
    expect(screen.getByText(/moving one bake earlier, or choosing a different recipe mix/i)).toBeInTheDocument();
    expect(sessionsApi.getSessionResults).not.toHaveBeenCalled();
  });

  it('uses structured one-oven guidance for partial sessions once results are available', async () => {
    vi.spyOn(sessionStatusHook, 'useSessionStatus').mockReturnValue({
      data: partialConflictSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });
    vi.spyOn(sessionsApi, 'getSessionResults').mockResolvedValue(resequencedResults);

    renderDetailPage(partialConflictSession.session_id);

    expect(await screen.findByText('One-oven schedule needs staging, not a full replan')).toBeInTheDocument();
    expect(screen.queryByText('One-oven conflict affected the original plan')).not.toBeInTheDocument();
    expect(screen.getByText('Completed with issues')).toBeInTheDocument();
  });

  it('falls back to prose conflict guidance on partial sessions only when result loading fails', async () => {
    vi.spyOn(sessionStatusHook, 'useSessionStatus').mockReturnValue({
      data: partialConflictSession,
      error: null,
      isPolling: false,
      refresh: vi.fn(),
    });
    vi.spyOn(sessionsApi, 'getSessionResults').mockRejectedValue(new Error('Results unavailable'));

    renderDetailPage(partialConflictSession.session_id);

    expect(await screen.findByText('Could not load results')).toBeInTheDocument();
    expect(screen.getByText('One-oven conflict affected the original plan')).toBeInTheDocument();
    expect(screen.getByText(/review the schedule below to see whether the planner found a staged sequence/i)).toBeInTheDocument();
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

  it('uses generated-planner metadata and recipe provenance in the PDF surface', () => {
    render(<RecipePDF session={menuSession} results={results} />);

    expect(screen.getByText('Generated plan')).toBeInTheDocument();
    expect(screen.getByText('Plan a Dinner')).toBeInTheDocument();
    expect(screen.getByText('A rustic Italian dinner with handmade pasta and seasonal vegetables')).toBeInTheDocument();
    expect(screen.getByText('Built from a fresh dinner brief inside the dinner planner.')).toBeInTheDocument();
    expect(screen.getByText('From your recipe library')).toBeInTheDocument();
    expect(screen.getByText('Generated for this session')).toBeInTheDocument();
    expect(screen.getByText('From your cookbook library')).toBeInTheDocument();
  });

  it('uses scheduler-provided burner metadata in schedule presentation without inventing labels for oven rows', () => {
    render(
      <ScheduleTimeline
        schedule={{
          ...baseSchedule,
          timeline: [
            {
              step_id: 'oven-step',
              recipe_name: 'Roast Chicken',
              action: 'Roast until browned',
              resource: 'oven',
              duration_minutes: 25,
              duration_max: null,
              label: 'T+10',
              time_offset_minutes: 10,
              clock_time: null,
              buffer_minutes: null,
              heads_up: null,
              is_prep_ahead: false,
              prep_ahead_window: null,
              prep_ahead_notes: null,
              merged_from: [],
              allocation: {},
              is_preheat: false,
              oven_temp_f: 425,
              burner_id: null,
              burner_position: null,
              burner_size: null,
              burner_label: null,
              burner: null,
            },
            {
              step_id: 'stovetop-step',
              recipe_name: 'Pan Sauce',
              action: 'Simmer sauce on burner Rear Right',
              resource: 'stovetop',
              duration_minutes: 12,
              duration_max: null,
              label: 'T+35',
              time_offset_minutes: 35,
              clock_time: null,
              buffer_minutes: null,
              heads_up: null,
              is_prep_ahead: false,
              prep_ahead_window: null,
              prep_ahead_notes: null,
              merged_from: [],
              allocation: {},
              is_preheat: false,
              oven_temp_f: null,
              burner_id: 'burner_2',
              burner_position: 'rear_right',
              burner_size: 'small',
              burner_label: 'Rear Right',
              burner: {
                burner_id: 'burner_2',
                position: 'rear_right',
                size: 'small',
                label: 'Rear Right',
              },
            },
          ],
        }}
      />,
    );

    expect(screen.getByText('Roast until browned')).toBeInTheDocument();
    expect(screen.getByText('425°F')).toBeInTheDocument();
    expect(screen.getByText('Simmer sauce on burner Rear Right')).toBeInTheDocument();
    expect(screen.queryByText(/burner.*roast until browned/i)).not.toBeInTheDocument();
  });

  it('renders scheduler-owned resequencing guidance from structured one-oven metadata', () => {
    render(
      <ScheduleTimeline
        schedule={{
          ...baseSchedule,
          timeline: [
            {
              step_id: 'oven-a',
              recipe_name: 'Braised Short Ribs',
              action: 'Bake until fork tender',
              resource: 'oven',
              duration_minutes: 120,
              duration_max: null,
              label: 'T+0',
              time_offset_minutes: 0,
              clock_time: null,
              buffer_minutes: null,
              heads_up: null,
              is_prep_ahead: false,
              prep_ahead_window: null,
              prep_ahead_notes: null,
              merged_from: [],
              allocation: {},
              is_preheat: false,
              oven_temp_f: 325,
              burner_id: null,
              burner_position: null,
              burner_size: null,
              burner_label: null,
              burner: null,
            },
          ],
          one_oven_conflict: {
            classification: 'resequence_required',
            tolerance_f: 15,
            has_second_oven: false,
            temperature_gap_f: 75,
            blocking_recipe_names: ['Braised Short Ribs', 'Chocolate Fondant'],
            affected_step_ids: ['ribs-bake', 'fondant-bake'],
            remediation: {
              requires_resequencing: true,
              suggested_actions: [
                'Bake Chocolate Fondant after Braised Short Ribs finishes.',
                'Prep the fondant batter earlier so it is ready when the oven frees up.',
              ],
              delaying_recipe_names: ['Chocolate Fondant'],
              blocking_recipe_names: ['Braised Short Ribs'],
              notes: 'Single-oven schedule remains feasible if dessert is staged after the braise window.',
            },
          },
        }}
      />,
    );

    const guidance = screen.getByRole('region', { name: 'One-oven guidance' });

    expect(screen.getByRole('heading', { name: 'One-oven plan still works' })).toBeInTheDocument();
    expect(screen.getByText(/scheduler already found a workable sequence/i)).toBeInTheDocument();
    expect(screen.getByText('Temperature gap: 75°F')).toBeInTheDocument();
    expect(screen.getByText('Bake first')).toBeInTheDocument();
    expect(within(guidance).getByText('Braised Short Ribs')).toBeInTheDocument();
    expect(screen.getByText('Stage later')).toBeInTheDocument();
    expect(within(guidance).getByText('Chocolate Fondant')).toBeInTheDocument();
    expect(screen.getByText('Bake Chocolate Fondant after Braised Short Ribs finishes.')).toBeInTheDocument();
    expect(screen.getByText('Prep the fondant batter earlier so it is ready when the oven frees up.')).toBeInTheDocument();
    expect(screen.getByText('Single-oven schedule remains feasible if dessert is staged after the braise window.')).toBeInTheDocument();
    expect(screen.queryByText('ribs-bake')).not.toBeInTheDocument();
  });

  it('stays quiet for compatible schedules and legacy payloads without one-oven metadata', () => {
    const { rerender } = render(
      <ScheduleTimeline
        schedule={{
          ...baseSchedule,
          timeline: [
            {
              step_id: 'compatible-step',
              recipe_name: 'Roast Chicken',
              action: 'Roast until browned',
              resource: 'oven',
              duration_minutes: 45,
              duration_max: null,
              label: 'T+0',
              time_offset_minutes: 0,
              clock_time: null,
              buffer_minutes: null,
              heads_up: null,
              is_prep_ahead: false,
              prep_ahead_window: null,
              prep_ahead_notes: null,
              merged_from: [],
              allocation: {},
              is_preheat: false,
              oven_temp_f: 400,
              burner_id: null,
              burner_position: null,
              burner_size: null,
              burner_label: null,
              burner: null,
            },
          ],
          one_oven_conflict: {
            classification: 'compatible',
            tolerance_f: 15,
            has_second_oven: false,
            temperature_gap_f: 10,
            blocking_recipe_names: ['Roast Chicken'],
            affected_step_ids: ['compatible-step'],
            remediation: {
              requires_resequencing: false,
              suggested_actions: ['No action needed'],
              delaying_recipe_names: [],
              blocking_recipe_names: [],
              notes: 'No conflict.',
            },
          },
        }}
      />,
    );

    expect(screen.queryByRole('heading', { name: 'One-oven plan still works' })).not.toBeInTheDocument();
    expect(screen.queryByText(/temperature gap:/i)).not.toBeInTheDocument();

    rerender(
      <ScheduleTimeline
        schedule={{
          ...baseSchedule,
          timeline: [
            {
              step_id: 'legacy-step',
              recipe_name: 'Salad',
              action: 'Dress the greens',
              resource: 'hands',
              duration_minutes: 5,
              duration_max: null,
              label: 'T+5',
              time_offset_minutes: 5,
              clock_time: null,
              buffer_minutes: null,
              heads_up: null,
              is_prep_ahead: false,
              prep_ahead_window: null,
              prep_ahead_notes: null,
              merged_from: [],
              allocation: {},
              is_preheat: false,
              oven_temp_f: null,
              burner_id: null,
              burner_position: null,
              burner_size: null,
              burner_label: null,
              burner: null,
            },
          ],
        }}
      />,
    );

    expect(screen.queryByRole('heading', { name: 'One-oven plan still works' })).not.toBeInTheDocument();
  });

  it('uses authored metadata in the PDF surface', () => {
    render(<RecipePDF session={authoredSession} results={results} />);

    expect(screen.getByText('Authored recipe')).toBeInTheDocument();
    expect(screen.getByText('Browse Recipe Library')).toBeInTheDocument();
    expect(screen.getAllByText('Chicken Ballotine with Tarragon Jus')).toHaveLength(2);
    expect(screen.getByText('Built from your private library so the session reflects a saved dish rather than a new menu brief.')).toBeInTheDocument();
  });
});

