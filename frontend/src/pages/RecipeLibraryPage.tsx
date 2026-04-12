import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { listAuthoredRecipes, updateAuthoredRecipeCookbook } from '../api/authoredRecipes';
import { createRecipeCookbook, listRecipeCookbooks } from '../api/recipeCookbooks';
import { createSession, runPipeline } from '../api/sessions';
import { pathwayByKey } from '../components/layout/pathways';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import {
  type AuthoredRecipeListItem,
  type CreateAuthoredSessionRequest,
  type MealType,
  type Occasion,
  type RecipeCookbookDetail,
} from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './RecipeLibraryPage.module.css';

type LibraryStatus = 'loading' | 'ready' | 'error';
type MoveState = Record<string, string>;
type ScheduleState = Record<string, 'starting'>;

const defaultMealType: MealType = 'dinner';
const defaultOccasion: Occasion = 'dinner_party';

function formatRecipeTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return 'Recently updated';
  }

  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date);
}

export function RecipeLibraryPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<LibraryStatus>('loading');
  const [recipes, setRecipes] = useState<AuthoredRecipeListItem[]>([]);
  const [cookbooks, setCookbooks] = useState<RecipeCookbookDetail[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [newCookbookName, setNewCookbookName] = useState('');
  const [newCookbookDescription, setNewCookbookDescription] = useState('');
  const [creatingCookbook, setCreatingCookbook] = useState(false);
  const [moveState, setMoveState] = useState<MoveState>({});
  const [scheduleState, setScheduleState] = useState<ScheduleState>({});

  const generatedPlanner = pathwayByKey['generated-planner'];
  const authoredWorkspace = pathwayByKey['authored-workspace'];

  const fetchLibrary = useCallback(async () => {
    setStatus('loading');
    setLoadError(null);
    setInlineError(null);

    try {
      const [recipeData, cookbookData] = await Promise.all([listAuthoredRecipes(), listRecipeCookbooks()]);
      setRecipes(recipeData);
      setCookbooks(cookbookData);
      setStatus('ready');
    } catch (error) {
      setLoadError(getErrorMessage(error, 'Could not load your recipe library.'));
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    void fetchLibrary();
  }, [fetchLibrary]);

  const recipesByCookbook = useMemo(() => {
    const grouped = new Map<string, AuthoredRecipeListItem[]>();
    cookbooks.forEach((cookbook) => grouped.set(cookbook.cookbook_id, []));

    recipes.forEach((recipe) => {
      if (recipe.cookbook_id && grouped.has(recipe.cookbook_id)) {
        grouped.get(recipe.cookbook_id)?.push(recipe);
      }
    });

    return grouped;
  }, [cookbooks, recipes]);

  const unassignedRecipes = useMemo(() => recipes.filter((recipe) => recipe.cookbook_id === null), [recipes]);

  const totalCookbookRecipes = useMemo(
    () => Array.from(recipesByCookbook.values()).reduce((count, items) => count + items.length, 0),
    [recipesByCookbook],
  );

  const handleCreateCookbook = useCallback(async () => {
    const name = newCookbookName.trim();
    const description = newCookbookDescription.trim();
    if (!name || !description) {
      setInlineError('Name the cookbook and say what kind of dishes belong there.');
      return;
    }

    setCreatingCookbook(true);
    setInlineError(null);

    try {
      const created = await createRecipeCookbook({ name, description });
      setCookbooks((current) => [created, ...current]);
      setNewCookbookName('');
      setNewCookbookDescription('');
    } catch (error) {
      setInlineError(getErrorMessage(error, 'Could not create that cookbook right now.'));
    } finally {
      setCreatingCookbook(false);
    }
  }, [newCookbookDescription, newCookbookName]);

  const handleMoveRecipe = useCallback(async (recipeId: string, cookbookId: string | null) => {
    setMoveState((current) => ({ ...current, [recipeId]: cookbookId ?? '__unassigned__' }));
    setInlineError(null);

    try {
      const updated = await updateAuthoredRecipeCookbook(recipeId, { cookbook_id: cookbookId });
      setRecipes((current) =>
        current.map((recipe) =>
          recipe.recipe_id === recipeId
            ? {
                ...recipe,
                cookbook_id: updated.cookbook_id,
                cookbook: updated.cookbook,
                updated_at: updated.updated_at,
              }
            : recipe,
        ),
      );
    } catch (error) {
      setInlineError(getErrorMessage(error, 'Could not move that recipe just now.'));
    } finally {
      setMoveState((current) => {
        const next = { ...current };
        delete next[recipeId];
        return next;
      });
    }
  }, []);

  const handleScheduleRecipe = useCallback(
    async (recipe: AuthoredRecipeListItem) => {
      const recipeId = recipe.recipe_id?.trim();
      const title = recipe.title?.trim();

      if (!recipeId || !title) {
        setScheduleError('This saved draft is missing its scheduling details. Reopen it in the authoring workspace before trying again.');
        return;
      }

      if (scheduleState[recipeId]) {
        return;
      }

      setScheduleState((current) => ({ ...current, [recipeId]: 'starting' }));
      setScheduleError(null);

      const request: CreateAuthoredSessionRequest = {
        concept_source: 'authored',
        free_text: `Schedule authored recipe: ${title}`,
        selected_authored_recipe: {
          recipe_id: recipeId,
          title,
        },
        guest_count: 4,
        meal_type: defaultMealType,
        occasion: defaultOccasion,
      };

      try {
        const session = await createSession(request);
        if (!session?.session_id) {
          throw new Error('Could not start scheduling from this recipe.');
        }

        const runResult = await runPipeline(session.session_id);
        if (!runResult?.session_id) {
          throw new Error('Could not start scheduling from this recipe.');
        }

        navigate(`/sessions/${session.session_id}`);
      } catch (error) {
        setScheduleError(getErrorMessage(error, 'Could not start scheduling from this recipe.'));
      } finally {
        setScheduleState((current) => {
          const next = { ...current };
          delete next[recipeId];
          return next;
        });
      }
    },
    [navigate, scheduleState],
  );

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.kicker}>Private recipe library</p>
          <h1 className={styles.title}>Keep your authored dishes on a shelf you can reopen at service speed.</h1>
          <p className={styles.subtitle}>
            Browse private drafts, tuck them into cookbook folders, and reopen any dish inside your owned library lane before you cross into dinner-planning sessions.
          </p>
          <div className={styles.heroActions}>
            <Link to={authoredWorkspace.to}>
              <Button>{authoredWorkspace.cta}</Button>
            </Link>
            <Link to={generatedPlanner.to} className={styles.secondaryLink}>
              Need a full service plan instead?
            </Link>
          </div>
        </div>

        <aside className={styles.heroAside} aria-label="Library health">
          <div className={styles.metricCard}>
            <p className={styles.metricLabel}>Library state</p>
            <p className={styles.metricValue}>
              {status === 'loading' ? 'Loading shelf…' : status === 'error' ? 'Shelf unavailable' : 'Shelf ready'}
            </p>
            <p className={styles.metricText}>
              {status === 'loading'
                ? 'Pulling cookbook folders and loose drafts into one private view.'
                : status === 'error'
                  ? 'The fetch failed before the library could be composed.'
                  : 'Loading, empty, and move errors stay visible here instead of hiding in devtools.'}
            </p>
          </div>

          <div className={styles.metricCard}>
            <p className={styles.metricLabel}>Pathway relationship</p>
            <p className={styles.metricValue}>Draft here. Plan there.</p>
            <p className={styles.metricText}>
              Use the shelf when a dish already exists. Move to <span className={styles.inlineEmphasis}>{generatedPlanner.title}</span> only when you are ready to schedule service, and return to <span className={styles.inlineEmphasis}>{authoredWorkspace.title}</span> when the recipe itself still needs writing.
            </p>
          </div>

          <div className={styles.metricRow}>
            <div>
              <span className={styles.metricNumber}>{recipes.length}</span>
              <span className={styles.metricCaption}>saved drafts</span>
            </div>
            <div>
              <span className={styles.metricNumber}>{cookbooks.length}</span>
              <span className={styles.metricCaption}>cookbook folders</span>
            </div>
            <div>
              <span className={styles.metricNumber}>{unassignedRecipes.length}</span>
              <span className={styles.metricCaption}>loose drafts</span>
            </div>
          </div>
        </aside>
      </header>

      <section className={styles.composer} aria-labelledby="cookbook-composer-title">
        <div className={styles.composerHeader}>
          <p className={styles.sectionEyebrow}>Cookbook folders</p>
          <h2 id="cookbook-composer-title" className={styles.sectionTitle}>
            Give recurring dishes a home.
          </h2>
          <p className={styles.sectionText}>
            These folders are private organization only — a way to shelve authored drafts by menu family, season, or cuisine.
          </p>
        </div>

        <div className={styles.composerForm}>
          <Input
            label="Cookbook name"
            value={newCookbookName}
            onChange={(event) => setNewCookbookName(event.target.value)}
            placeholder="Dessert"
          />
          <Textarea
            label="What belongs here?"
            value={newCookbookDescription}
            onChange={(event) => setNewCookbookDescription(event.target.value)}
            placeholder="Sweet finishes, plated fruit, and frozen components."
            rows={2}
          />
          <Button type="button" onClick={() => void handleCreateCookbook()} disabled={creatingCookbook}>
            {creatingCookbook ? 'Creating…' : 'Create Cookbook'}
          </Button>
        </div>
      </section>

      {inlineError ? (
        <div className={styles.inlineError} role="alert">
          {inlineError}
        </div>
      ) : null}

      {scheduleError ? (
        <div className={styles.inlineError} role="alert">
          {scheduleError}
        </div>
      ) : null}

      {status === 'loading' ? (
        <section className={styles.loadingState} aria-label="Loading recipe library">
          <div className={styles.loadingCard} />
          <div className={styles.loadingCard} />
          <div className={styles.loadingCard} />
        </section>
      ) : status === 'error' ? (
        <section className={styles.errorState} aria-live="polite">
          <p className={styles.errorEyebrow}>Library fetch failed</p>
          <h2 className={styles.errorTitle}>The shelf did not load.</h2>
          <p className={styles.errorText}>{loadError ?? 'Could not load your recipe library.'}</p>
          <Button variant="secondary" onClick={() => void fetchLibrary()}>
            Try again
          </Button>
        </section>
      ) : recipes.length === 0 && cookbooks.length === 0 ? (
        <section className={styles.emptyState} aria-live="polite">
          <div className={styles.emptyPlate} aria-hidden="true">
            <span className={styles.emptyPlateRing} />
            <span className={styles.emptyPlateLine} />
          </div>
          <p className={styles.emptyEyebrow}>Private shelf is empty</p>
          <h2 className={styles.emptyTitle}>No saved dishes yet.</h2>
          <p className={styles.emptyText}>
            Start a chef-authored draft, then return here when you want to browse or group it into a cookbook folder.
          </p>
          <div className={styles.emptyActions}>
            <Link to={authoredWorkspace.to}>
              <Button>{authoredWorkspace.title}</Button>
            </Link>
            <Link to={generatedPlanner.to} className={styles.secondaryLink}>
              Planning a whole dinner instead?
            </Link>
          </div>
        </section>
      ) : (
        <div className={styles.libraryGrid}>
          <section className={styles.libraryColumn} aria-labelledby="unassigned-recipes-title">
            <div className={styles.sectionHeader}>
              <div>
                <p className={styles.sectionEyebrow}>Loose drafts</p>
                <h2 id="unassigned-recipes-title" className={styles.sectionTitle}>
                  Unassigned recipes
                </h2>
              </div>
              <span className={styles.countPill}>{unassignedRecipes.length}</span>
            </div>

            {unassignedRecipes.length === 0 ? (
              <p className={styles.sectionEmpty}>Every saved draft is currently tucked into a cookbook folder.</p>
            ) : (
              <div className={styles.recipeStack}>
                {unassignedRecipes.map((recipe) => (
                  <article key={recipe.recipe_id} className={styles.recipeCard}>
                    <div className={styles.recipeCardHeader}>
                      <div>
                        <p className={styles.recipeKicker}>Loose draft</p>
                        <h3 className={styles.recipeTitle}>{recipe.title}</h3>
                      </div>
                      <span className={styles.recipeMeta}>{formatRecipeTimestamp(recipe.updated_at)}</span>
                    </div>
                    <p className={styles.recipeCuisine}>{recipe.cuisine}</p>
                    <div className={styles.recipeActions}>
                      <Link to={`/recipes/new?recipeId=${recipe.recipe_id}`} className={styles.recipeLink}>
                        Reopen in workspace
                      </Link>
                      <div className={styles.recipeActionCluster}>
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => void handleScheduleRecipe(recipe)}
                          disabled={Boolean(scheduleState[recipe.recipe_id])}
                        >
                          {scheduleState[recipe.recipe_id] ? 'Starting schedule…' : 'Schedule from shelf'}
                        </Button>
                        <label className={styles.selectField}>
                          <span className={styles.selectLabel}>Move to cookbook</span>
                          <select
                            value={moveState[recipe.recipe_id] ?? '__none__'}
                            onChange={(event) => {
                              const value = event.target.value;
                              if (value !== '__none__') {
                                void handleMoveRecipe(recipe.recipe_id, value);
                              }
                            }}
                            disabled={cookbooks.length === 0 || recipe.recipe_id in moveState}
                          >
                            <option value="__none__">Choose a cookbook</option>
                            {cookbooks.map((cookbook) => (
                              <option key={cookbook.cookbook_id} value={cookbook.cookbook_id}>
                                {cookbook.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className={styles.libraryColumn} aria-labelledby="cookbook-shelves-title">
            <div className={styles.sectionHeader}>
              <div>
                <p className={styles.sectionEyebrow}>Cookbook shelves</p>
                <h2 id="cookbook-shelves-title" className={styles.sectionTitle}>
                  Organized folders
                </h2>
              </div>
              <span className={styles.countPill}>{totalCookbookRecipes}</span>
            </div>

            {cookbooks.length === 0 ? (
              <p className={styles.sectionEmpty}>Create your first cookbook folder to group related authored dishes.</p>
            ) : (
              <div className={styles.cookbookStack}>
                {cookbooks.map((cookbook) => {
                  const cookbookRecipes = recipesByCookbook.get(cookbook.cookbook_id) ?? [];

                  return (
                    <article key={cookbook.cookbook_id} className={styles.cookbookCard}>
                      <div className={styles.cookbookHeader}>
                        <div>
                          <p className={styles.recipeKicker}>Cookbook folder</p>
                          <h3 className={styles.cookbookTitle}>{cookbook.name}</h3>
                        </div>
                        <span className={styles.countPill}>{cookbookRecipes.length}</span>
                      </div>
                      <p className={styles.cookbookDescription}>{cookbook.description}</p>

                      {cookbookRecipes.length === 0 ? (
                        <p className={styles.sectionEmpty}>No authored drafts shelved here yet.</p>
                      ) : (
                        <div className={styles.recipeStack}>
                          {cookbookRecipes.map((recipe) => (
                            <article key={recipe.recipe_id} className={styles.recipeCardInset}>
                              <div className={styles.recipeCardHeader}>
                                <div>
                                  <p className={styles.recipeKicker}>Saved draft</p>
                                  <h4 className={styles.recipeTitle}>{recipe.title}</h4>
                                </div>
                                <span className={styles.recipeMeta}>{formatRecipeTimestamp(recipe.updated_at)}</span>
                              </div>
                              <p className={styles.recipeCuisine}>{recipe.cuisine}</p>
                              <div className={styles.recipeActions}>
                                <Link to={`/recipes/new?recipeId=${recipe.recipe_id}`} className={styles.recipeLink}>
                                  Reopen in workspace
                                </Link>
                                <div className={styles.recipeActionCluster}>
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => void handleScheduleRecipe(recipe)}
                                    disabled={Boolean(scheduleState[recipe.recipe_id])}
                                  >
                                    {scheduleState[recipe.recipe_id] ? 'Starting schedule…' : 'Schedule service'}
                                  </Button>
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => void handleMoveRecipe(recipe.recipe_id, null)}
                                    disabled={recipe.recipe_id in moveState}
                                  >
                                    Remove from cookbook
                                  </Button>
                                </div>
                              </div>
                            </article>
                          ))}
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
