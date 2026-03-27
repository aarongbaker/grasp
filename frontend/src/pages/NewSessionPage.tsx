import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listDetectedCookbookRecipes } from '../api/ingest';
import { createSession, runPipeline } from '../api/sessions';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import {
  MEAL_TYPE_LABELS,
  OCCASION_LABELS,
  type CookbookSelectionSummary,
  type CreateCookbookSessionSelectionPayload,
  type DetectedCookbookRecipe,
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

function toSelectedCookbookRecipe(recipe: DetectedCookbookRecipe): SelectedCookbookRecipe {
  return {
    chunk_id: recipe.chunk_id,
    book_id: recipe.book_id,
    book_title: recipe.book_title,
    chapter: recipe.chapter,
    page_number: recipe.page_number,
    selection_order: 0,
  };
}

function buildRecipePreview(recipe: DetectedCookbookRecipe): string {
  const trimmedText = recipe.text.trim();
  if (trimmedText.length <= 220) {
    return trimmedText;
  }
  return `${trimmedText.slice(0, 217).trimEnd()}...`;
}

function buildRecipeMeta(recipe: DetectedCookbookRecipe): string {
  const meta = [] as string[];
  if (recipe.chapter) {
    meta.push(recipe.chapter);
  }
  if (recipe.page_number !== null) {
    meta.push(`Page ${recipe.page_number}`);
  }
  return meta.join(' • ');
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
  const [selectedCookbookRecipes, setSelectedCookbookRecipes] = useState<SelectedCookbookRecipe[]>([]);
  const [detectedRecipes, setDetectedRecipes] = useState<DetectedCookbookRecipe[]>([]);
  const [cookbookLoading, setCookbookLoading] = useState(false);
  const [cookbookError, setCookbookError] = useState('');
  const [cookbookFetched, setCookbookFetched] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const cookbookPayload = useMemo(
    () => buildCookbookSelectionPayload(selectedCookbookRecipes),
    [selectedCookbookRecipes],
  );
  const cookbookSummary = useMemo(() => summarizeCookbookSelection(cookbookPayload), [cookbookPayload]);
  const cookbookSubmitDisabled = loading || cookbookSummary.total_selected === 0;

  const recipesByBook = useMemo(() => {
    const grouped = new Map<string, { bookId: string; bookTitle: string; recipes: DetectedCookbookRecipe[] }>();
    for (const recipe of detectedRecipes) {
      const existing = grouped.get(recipe.book_id);
      if (existing) {
        existing.recipes.push(recipe);
      } else {
        grouped.set(recipe.book_id, {
          bookId: recipe.book_id,
          bookTitle: recipe.book_title,
          recipes: [recipe],
        });
      }
    }
    return Array.from(grouped.values());
  }, [detectedRecipes]);

  useEffect(() => {
    if (mode !== 'cookbook_recipes' || cookbookFetched) {
      return;
    }

    let active = true;
    setCookbookLoading(true);
    setCookbookError('');

    void listDetectedCookbookRecipes()
      .then((recipes) => {
        if (!active) {
          return;
        }
        setDetectedRecipes(recipes);
        setCookbookFetched(true);
      })
      .catch((err: unknown) => {
        if (!active) {
          return;
        }
        setCookbookError(getErrorMessage(err, 'Unable to load cookbook recipes right now.'));
      })
      .finally(() => {
        if (active) {
          setCookbookLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [cookbookFetched, mode]);

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

  function toggleCookbookRecipe(recipe: DetectedCookbookRecipe) {
    setSelectedCookbookRecipes((current) => {
      const alreadySelected = current.some((selected) => selected.chunk_id === recipe.chunk_id);
      if (alreadySelected) {
        return current.filter((selected) => selected.chunk_id !== recipe.chunk_id);
      }
      return [...current, toSelectedCookbookRecipe(recipe)];
    });
    setError('');
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

  function renderCookbookPickerBody() {
    if (cookbookLoading) {
      return (
        <div className={styles.pickerStateCard} role="status">
          Loading cookbook recipe candidates...
        </div>
      );
    }

    if (cookbookError) {
      return (
        <div className={styles.pickerStateCard} role="alert">
          {cookbookError}
        </div>
      );
    }

    if (recipesByBook.length === 0) {
      return (
        <div className={styles.pickerStateCard}>
          No detected cookbook recipes yet. Upload a cookbook to browse recipe candidates here.
        </div>
      );
    }

    return (
      <div className={styles.recipeGroups}>
        {recipesByBook.map((group) => (
          <section key={group.bookId} className={styles.recipeGroup} aria-labelledby={`book-group-${group.bookId}`}>
            <div className={styles.recipeGroupHeader}>
              <h3 id={`book-group-${group.bookId}`} className={styles.recipeGroupTitle}>
                {group.bookTitle}
              </h3>
              <p className={styles.recipeGroupMeta}>
                {group.recipes.length} candidate{group.recipes.length === 1 ? '' : 's'}
              </p>
            </div>
            <div className={styles.recipeCards}>
              {group.recipes.map((recipe) => {
                const selected = cookbookSummary.selected_chunk_ids.includes(recipe.chunk_id);
                const preview = buildRecipePreview(recipe);
                const meta = buildRecipeMeta(recipe);
                return (
                  <label
                    key={recipe.chunk_id}
                    className={`${styles.recipeCard} ${selected ? styles.recipeCardSelected : ''}`}
                  >
                    <input
                      type="checkbox"
                      className={styles.recipeCheckbox}
                      checked={selected}
                      onChange={() => toggleCookbookRecipe(recipe)}
                      aria-label={`Select ${recipe.chunk_id} from ${recipe.book_title}`}
                    />
                    <div className={styles.recipeCardBody}>
                      <div className={styles.recipeCardHeader}>
                        <span className={styles.recipeCardType}>{recipe.chunk_type.replace(/_/g, ' ')}</span>
                        {meta && <span className={styles.recipeCardMeta}>{meta}</span>}
                      </div>
                      <p className={styles.recipeCardPreview}>{preview}</p>
                    </div>
                  </label>
                );
              })}
            </div>
          </section>
        ))}
      </div>
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
              Choose recipe candidates from your uploaded books. This picker prepares a stable selection payload now, and cookbook session creation lands in S03.
            </p>
          </div>

          {renderCookbookPickerBody()}

          <div className={styles.selectionSummary}>
            <p className={styles.selectionSummaryTitle}>Selected recipes</p>
            <p className={styles.selectionSummaryBody}>
              {cookbookSummary.total_selected === 0
                ? 'No cookbook recipes selected yet.'
                : `${cookbookSummary.total_selected} recipes selected across ${cookbookSummary.selected_book_ids.length} books.`}
            </p>
            {cookbookSummary.total_selected > 0 && (
              <ul className={styles.selectionList} aria-label="Selected cookbook chunks">
                {cookbookPayload.selected_recipes.map((recipe) => (
                  <li key={recipe.chunk_id} className={styles.selectionListItem}>
                    <span className={styles.selectionListTitle}>{recipe.book_title}</span>
                    <span className={styles.selectionListMeta}>
                      Chunk {recipe.chunk_id}
                      {recipe.page_number !== null ? ` • Page ${recipe.page_number}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
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
          <p className={styles.helperText}>
            {cookbookSummary.total_selected === 0
              ? 'Select at least one cookbook recipe to continue. Backend cookbook session creation arrives in S03.'
              : 'Backend cookbook session creation arrives in S03.'}
          </p>
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
