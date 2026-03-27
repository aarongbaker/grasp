import { type FormEvent, type KeyboardEvent, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createSession, runPipeline } from '../api/sessions';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import {
  MEAL_TYPE_LABELS,
  OCCASION_LABELS,
  type CookbookSelectionSummary,
  type CreateCookbookSessionSelectionPayload,
  type MealType,
  type NewSessionMode,
  type Occasion,
  type SelectedCookbookRecipe,
} from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './NewSessionPage.module.css';

const mealTypeOptions = Object.entries(MEAL_TYPE_LABELS).map(([value, label]) => ({ value, label }));
const occasionOptions = Object.entries(OCCASION_LABELS).map(([value, label]) => ({ value, label }));

const sessionModeOptions: Array<{ value: NewSessionMode; label: string; description: string }> = [
  {
    value: 'meal_ideas',
    label: 'Meal ideas',
    description: 'Describe the dinner you want and generate a new plan from scratch.',
  },
  {
    value: 'cookbook_recipes',
    label: 'Cookbook recipes',
    description: 'Choose recipe candidates from your uploaded books before creating a session.',
  },
];

function buildCookbookSelectionPayload(selectedRecipes: SelectedCookbookRecipe[]): CreateCookbookSessionSelectionPayload {
  return {
    mode: 'cookbook_recipes',
    selected_recipes: selectedRecipes.map((recipe, index) => ({
      ...recipe,
      selection_order: index,
    })),
  };
}

function summarizeCookbookSelection(payload: CreateCookbookSessionSelectionPayload): CookbookSelectionSummary {
  return {
    total_selected: payload.selected_recipes.length,
    selected_book_ids: [...new Set(payload.selected_recipes.map((recipe) => recipe.book_id))],
    selected_chunk_ids: payload.selected_recipes.map((recipe) => recipe.chunk_id),
  };
}

export function NewSessionPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<NewSessionMode>('meal_ideas');
  const [freeText, setFreeText] = useState('');
  const [guestCount, setGuestCount] = useState(4);
  const [mealType, setMealType] = useState<MealType>('dinner');
  const [occasion, setOccasion] = useState<Occasion>('dinner_party');
  const [restrictions, setRestrictions] = useState<string[]>([]);
  const [restrictionInput, setRestrictionInput] = useState('');
  const [servingTime, setServingTime] = useState('');
  const [selectedCookbookRecipes] = useState<SelectedCookbookRecipe[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const cookbookPayload = useMemo(
    () => buildCookbookSelectionPayload(selectedCookbookRecipes),
    [selectedCookbookRecipes],
  );
  const cookbookSummary = useMemo(() => summarizeCookbookSelection(cookbookPayload), [cookbookPayload]);
  const cookbookSubmitDisabled = loading || cookbookSummary.total_selected === 0;

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

  async function handleMealIdeaSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const session = await createSession({
        free_text: freeText,
        guest_count: guestCount,
        meal_type: mealType,
        occasion,
        dietary_restrictions: restrictions,
        serving_time: servingTime || undefined,
      });
      await runPipeline(session.session_id);
      navigate(`/sessions/${session.session_id}`);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Something went wrong — please try again'));
    } finally {
      setLoading(false);
    }
  }

  function handleCookbookSubmit(e: FormEvent) {
    e.preventDefault();
    if (cookbookSubmitDisabled) {
      setError('Select at least one cookbook recipe to continue.');
      return;
    }
  }

  function renderMealIdeaMode() {
    return (
      <form className={styles.form} onSubmit={handleMealIdeaSubmit}>
        {error && <div className={styles.error}>{error}</div>}

        <Textarea
          label="What are you cooking?"
          placeholder="A rustic Italian dinner with handmade pasta, seasonal vegetables, and something decadent for dessert..."
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          maxLength={2000}
          required
        />

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
          <Button type="submit" disabled={loading || !freeText.trim()}>
            {loading ? 'Starting...' : 'Start Planning'}
          </Button>
          <Button type="button" variant="secondary" onClick={() => navigate('/')}>
            Cancel
          </Button>
        </div>
      </form>
    );
  }

  function renderCookbookMode() {
    return (
      <form className={styles.form} onSubmit={handleCookbookSubmit}>
        {error && <div className={styles.error}>{error}</div>}

        <section className={styles.modePanel} aria-labelledby="cookbook-mode-heading">
          <div className={styles.panelHeader}>
            <h2 id="cookbook-mode-heading" className={styles.panelTitle}>
              Browse cookbook recipes
            </h2>
            <p className={styles.panelDescription}>
              Choose recipe candidates from your uploaded books. We&apos;ll keep the selection payload stable while the picker lands in the next tasks.
            </p>
          </div>

          <div className={styles.placeholderCard}>
            <p className={styles.placeholderTitle}>Cookbook picker coming next</p>
            <p className={styles.placeholderBody}>
              Recipe candidates will appear here once the picker fetch flow is connected.
            </p>
          </div>

          <div className={styles.selectionSummary}>
            <p className={styles.selectionSummaryTitle}>Selected recipes</p>
            <p className={styles.selectionSummaryBody}>
              {cookbookSummary.total_selected === 0
                ? 'No cookbook recipes selected yet.'
                : `${cookbookSummary.total_selected} recipes selected across ${cookbookSummary.selected_book_ids.length} books.`}
            </p>
          </div>
        </section>

        <div className={styles.actions}>
          <Button type="submit" disabled={cookbookSubmitDisabled}>
            {loading ? 'Starting...' : 'Start Cookbook Session'}
          </Button>
          <Button type="button" variant="secondary" onClick={() => navigate('/')}>
            Cancel
          </Button>
        </div>

        {cookbookSubmitDisabled && (
          <p className={styles.helperText}>Select at least one cookbook recipe to continue.</p>
        )}
      </form>
    );
  }

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Plan a Dinner</h1>
      <p className={styles.subtitle}>Switch between freeform meal ideas and cookbook recipe picks without changing the existing planning flow.</p>

      <div className={styles.modeSwitcher} role="tablist" aria-label="Session mode">
        {sessionModeOptions.map((option) => {
          const active = option.value === mode;
          return (
            <button
              key={option.value}
              type="button"
              role="tab"
              aria-selected={active}
              className={`${styles.modeButton} ${active ? styles.modeButtonActive : ''}`}
              onClick={() => {
                setMode(option.value);
                setError('');
              }}
            >
              <span className={styles.modeButtonLabel}>{option.label}</span>
              <span className={styles.modeButtonDescription}>{option.description}</span>
            </button>
          );
        })}
      </div>

      {mode === 'meal_ideas' ? renderMealIdeaMode() : renderCookbookMode()}
    </div>
  );
}
