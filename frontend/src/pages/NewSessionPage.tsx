import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { listAuthoredRecipes } from '../api/authoredRecipes';
import { listRecipeCookbooks } from '../api/recipeCookbooks';
import { createSession, runPipeline } from '../api/sessions';
import { pathwayByKey } from '../components/layout/pathways';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import {
  MEAL_TYPE_LABELS,
  OCCASION_LABELS,
  type AuthoredRecipeListItem,
  type CreateFreeTextSessionRequest,
  type CreatePlannerAuthoredAnchorSessionRequest,
  type CreatePlannerCookbookTargetSessionRequest,
  type CreateSessionRequest,
  type MealType,
  type Occasion,
  type RecipeCookbookDetail,
} from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './NewSessionPage.module.css';

const mealTypeOptions = Object.entries(MEAL_TYPE_LABELS).map(([value, label]) => ({ value, label }));
const occasionOptions = Object.entries(OCCASION_LABELS).map(([value, label]) => ({ value, label }));

const plannerAnchorOptions = [
  { value: 'none', label: 'Menu intent only' },
  { value: 'authored', label: 'Saved recipe anchor' },
  { value: 'cookbook', label: 'Cookbook target' },
] as const;

type PlannerAnchorMode = (typeof plannerAnchorOptions)[number]['value'];
type PlannerLibraryStatus = 'loading' | 'ready' | 'error';

export function NewSessionPage() {
  const navigate = useNavigate();
  const [freeText, setFreeText] = useState('');
  const [guestCount, setGuestCount] = useState(4);
  const [mealType, setMealType] = useState<MealType>('dinner');
  const [occasion, setOccasion] = useState<Occasion>('dinner_party');
  const [restrictions, setRestrictions] = useState<string[]>([]);
  const [restrictionInput, setRestrictionInput] = useState('');
  const [servingTime, setServingTime] = useState('');
  const [plannerAnchorMode, setPlannerAnchorMode] = useState<PlannerAnchorMode>('none');
  const [plannerRecipes, setPlannerRecipes] = useState<AuthoredRecipeListItem[]>([]);
  const [plannerCookbooks, setPlannerCookbooks] = useState<RecipeCookbookDetail[]>([]);
  const [selectedPlannerRecipeId, setSelectedPlannerRecipeId] = useState('');
  const [selectedPlannerCookbookId, setSelectedPlannerCookbookId] = useState('');
  const [plannerLibraryStatus, setPlannerLibraryStatus] = useState<PlannerLibraryStatus>('loading');
  const [plannerLibraryError, setPlannerLibraryError] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const recipeLibrary = pathwayByKey['recipe-library'];
  const authoredWorkspace = pathwayByKey['authored-workspace'];
  const crossLinks = useMemo(
    () => [
      {
        label: recipeLibrary.title,
        to: recipeLibrary.to,
        description: 'Reach for the shelf when the dish already exists and you want to reopen or schedule it.',
      },
      {
        label: authoredWorkspace.title,
        to: authoredWorkspace.to,
        description: 'Use the workspace first when you need to draft the dish itself before it belongs in service planning.',
      },
    ],
    [authoredWorkspace.title, authoredWorkspace.to, recipeLibrary.title, recipeLibrary.to],
  );

  useEffect(() => {
    let active = true;

    async function loadPlannerLibrary() {
      setPlannerLibraryStatus('loading');
      setPlannerLibraryError('');

      try {
        const [recipes, cookbooks] = await Promise.all([listAuthoredRecipes(), listRecipeCookbooks()]);
        if (!active) {
          return;
        }
        setPlannerRecipes(recipes);
        setPlannerCookbooks(cookbooks);
        setPlannerLibraryStatus('ready');
      } catch (err: unknown) {
        if (!active) {
          return;
        }
        setPlannerLibraryStatus('error');
        setPlannerLibraryError(getErrorMessage(err, 'Could not load your saved recipes and cookbooks.'));
      }
    }

    void loadPlannerLibrary();

    return () => {
      active = false;
    };
  }, []);

  const plannerRecipeOptions = useMemo(
    () => [
      { value: '', label: plannerRecipes.length > 0 ? 'Choose one saved recipe' : 'No saved recipes yet' },
      ...plannerRecipes.map((recipe) => ({ value: recipe.recipe_id, label: recipe.title })),
    ],
    [plannerRecipes],
  );

  const plannerCookbookOptions = useMemo(
    () => [
      { value: '', label: plannerCookbooks.length > 0 ? 'Choose one cookbook' : 'No cookbooks yet' },
      ...plannerCookbooks.map((cookbook) => ({ value: cookbook.cookbook_id, label: cookbook.name })),
    ],
    [plannerCookbooks],
  );

  const selectedPlannerRecipe = useMemo(
    () => plannerRecipes.find((recipe) => recipe.recipe_id === selectedPlannerRecipeId) ?? null,
    [plannerRecipes, selectedPlannerRecipeId],
  );

  const selectedPlannerCookbook = useMemo(
    () => plannerCookbooks.find((cookbook) => cookbook.cookbook_id === selectedPlannerCookbookId) ?? null,
    [plannerCookbooks, selectedPlannerCookbookId],
  );

  function addRestriction(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const val = restrictionInput.trim();
      if (val && !restrictions.includes(val)) {
        setRestrictions([...restrictions, val]);
      }
      setRestrictionInput('');
    }
  }

  function handlePlannerAnchorModeChange(value: PlannerAnchorMode) {
    setPlannerAnchorMode(value);
    setError('');
    if (value !== 'authored') {
      setSelectedPlannerRecipeId('');
    }
    if (value !== 'cookbook') {
      setSelectedPlannerCookbookId('');
    }
  }

  function buildRequest(): CreateSessionRequest {
    const sharedFields = {
      free_text: freeText,
      guest_count: guestCount,
      meal_type: mealType,
      occasion,
      dietary_restrictions: restrictions,
      serving_time: servingTime || undefined,
    };

    if (plannerAnchorMode === 'authored') {
      if (!selectedPlannerRecipe) {
        throw new Error('Choose one saved recipe anchor before starting the plan.');
      }

      const request: CreatePlannerAuthoredAnchorSessionRequest = {
        concept_source: 'planner_authored_anchor',
        ...sharedFields,
        planner_authored_recipe_anchor: {
          recipe_id: selectedPlannerRecipe.recipe_id,
          title: selectedPlannerRecipe.title,
        },
      };
      return request;
    }

    if (plannerAnchorMode === 'cookbook') {
      if (!selectedPlannerCookbook) {
        throw new Error('Choose one cookbook target before starting the plan.');
      }

      const request: CreatePlannerCookbookTargetSessionRequest = {
        concept_source: 'planner_cookbook_target',
        ...sharedFields,
        planner_cookbook_target: {
          cookbook_id: selectedPlannerCookbook.cookbook_id,
          name: selectedPlannerCookbook.name,
        },
      };
      return request;
    }

    const request: CreateFreeTextSessionRequest = {
      ...sharedFields,
      concept_source: 'free_text',
    };
    return request;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');

    let request: CreateSessionRequest;
    try {
      request = buildRequest();
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Something went wrong — please try again'));
      return;
    }

    setLoading(true);
    try {
      const session = await createSession(request);
      await runPipeline(session.session_id);
      navigate(`/sessions/${session.session_id}`);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Something went wrong — please try again'));
    } finally {
      setLoading(false);
    }
  }

  const canSubmit = !!freeText.trim();
  const plannerAnchorDescription =
    plannerAnchorMode === 'authored'
      ? 'Ground the plan in one owned authored recipe while keeping the rest of the dinner brief open.'
      : plannerAnchorMode === 'cookbook'
        ? 'Point the planner at one cookbook shelf so the brief stays inside that owned collection.'
        : 'Start from menu intent alone when you want the planner to build the dinner from a clean brief.';

  return (
    <div className={styles.page}>
      <div className={styles.hero}>
        <div>
          <h1 className={styles.title}>Plan a Dinner</h1>
          <p className={styles.subtitle}>
            Describe the meal you want to cook. GRASP will turn that menu intent into a paced dinner service with
            timing, equipment flow, and a finished schedule.
          </p>
        </div>

        <aside className={styles.guidanceCard} aria-labelledby="planner-lane-heading">
          <p className={styles.guidanceEyebrow}>Planner lane</p>
          <h2 id="planner-lane-heading" className={styles.guidanceTitle}>
            Start here when service timing leads.
          </h2>
          <p className={styles.guidanceText}>
            Keep this route for menu-intent planning. It stays focused on a single dinner brief and does not switch into
            cookbook browsing or authored drafting.
          </p>
          <div className={styles.guidanceLinks}>
            {crossLinks.map((link) => (
              <Link key={link.to} to={link.to} className={styles.guidanceLink}>
                <span className={styles.guidanceLinkLabel}>{link.label}</span>
                <span className={styles.guidanceLinkText}>{link.description}</span>
              </Link>
            ))}
          </div>
        </aside>
      </div>

      <form className={styles.form} onSubmit={handleSubmit}>
        {error && <div className={styles.error}>{error}</div>}

        <Textarea
          label="What are you cooking?"
          placeholder="A rustic Italian dinner with handmade pasta, seasonal vegetables, and something decadent for dessert..."
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          maxLength={2000}
          required
        />

        <section className={styles.anchorCard} aria-labelledby="planner-anchor-heading">
          <div className={styles.anchorHeader}>
            <div>
              <p className={styles.anchorEyebrow}>Planner reference</p>
              <h2 id="planner-anchor-heading" className={styles.anchorTitle}>
                Keep the planner in one lane, with one owned anchor when needed.
              </h2>
            </div>
            <p className={styles.anchorDescription}>{plannerAnchorDescription}</p>
          </div>

          <div className={styles.anchorGrid}>
            <Select
              label="Planner anchor"
              options={plannerAnchorOptions.map((option) => ({ value: option.value, label: option.label }))}
              value={plannerAnchorMode}
              onChange={(e) => handlePlannerAnchorModeChange(e.target.value as PlannerAnchorMode)}
            />

            {plannerAnchorMode === 'authored' && (
              <Select
                label="Saved recipe anchor"
                options={plannerRecipeOptions}
                value={selectedPlannerRecipeId}
                onChange={(e) => setSelectedPlannerRecipeId(e.target.value)}
                disabled={plannerLibraryStatus !== 'ready' || plannerRecipes.length === 0}
              />
            )}

            {plannerAnchorMode === 'cookbook' && (
              <Select
                label="Cookbook target"
                options={plannerCookbookOptions}
                value={selectedPlannerCookbookId}
                onChange={(e) => setSelectedPlannerCookbookId(e.target.value)}
                disabled={plannerLibraryStatus !== 'ready' || plannerCookbooks.length === 0}
              />
            )}
          </div>

          <div className={styles.anchorMeta}>
            <span className={styles.anchorMetaLabel}>
              {plannerLibraryStatus === 'loading'
                ? 'Loading owned planner references…'
                : plannerLibraryStatus === 'error'
                  ? plannerLibraryError
                  : `${plannerRecipes.length} saved recipe${plannerRecipes.length === 1 ? '' : 's'} and ${plannerCookbooks.length} cookbook${plannerCookbooks.length === 1 ? '' : 's'} ready.`}
            </span>
            {plannerLibraryStatus === 'ready' && plannerAnchorMode === 'authored' && selectedPlannerRecipe && (
              <span className={styles.anchorMetaDetail}>Anchored to “{selectedPlannerRecipe.title}”.</span>
            )}
            {plannerLibraryStatus === 'ready' && plannerAnchorMode === 'cookbook' && selectedPlannerCookbook && (
              <span className={styles.anchorMetaDetail}>Targeting the “{selectedPlannerCookbook.name}” shelf.</span>
            )}
          </div>
        </section>

        <div className={styles.row}>
          <Input
            label="Guests"
            type="number"
            min={1}
            max={100}
            value={guestCount}
            onChange={(e) => setGuestCount(Number(e.target.value))}
          />
          <Select
            label="Meal type"
            options={mealTypeOptions}
            value={mealType}
            onChange={(e) => setMealType(e.target.value as MealType)}
          />
          <Input
            label="Serving time"
            type="time"
            value={servingTime}
            onChange={(e) => setServingTime(e.target.value)}
          />
        </div>

        <Select
          label="Occasion"
          options={occasionOptions}
          value={occasion}
          onChange={(e) => setOccasion(e.target.value as Occasion)}
        />

        <div>
          <Input
            label="Dietary restrictions"
            placeholder="Type and press Enter"
            value={restrictionInput}
            onChange={(e) => setRestrictionInput(e.target.value)}
            onKeyDown={addRestriction}
          />
          {restrictions.length > 0 && (
            <div className={styles.tags}>
              {restrictions.map((r) => (
                <span key={r} className={styles.tag}>
                  {r}
                  <button
                    type="button"
                    className={styles.tagRemove}
                    onClick={() => setRestrictions(restrictions.filter((x) => x !== r))}
                    aria-label={`Remove ${r}`}
                  >
                    x
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className={styles.actions}>
          <Button type="submit" disabled={loading || !canSubmit}>
            {loading ? 'Starting...' : 'Start Planning'}
          </Button>
          <Button type="button" variant="secondary" onClick={() => navigate('/')}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
