import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listDetectedRecipes } from '../api/ingest';
import { createSession, runPipeline } from '../api/sessions';
import { buildCookbookCandidatePreview } from '../components/session/cookbookCandidatePreview';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import {
  MEAL_TYPE_LABELS,
  OCCASION_LABELS,
  type CreateCookbookSessionRequest,
  type CreateFreeTextSessionRequest,
  type DetectedRecipeCandidate,
  type MealType,
  type Occasion,
  type SelectedCookbookRecipeRef,
  type SessionConceptSource,
} from '../types/api';
import { getErrorMessage } from '../utils/errors';
import { getRecipeDisplayTitle } from '../utils/cookbookTitles';
import styles from './NewSessionPage.module.css';

const EXCERPT_COLLAPSE_THRESHOLD = 120;

const mealTypeOptions = Object.entries(MEAL_TYPE_LABELS).map(([value, label]) => ({ value, label }));
const occasionOptions = Object.entries(OCCASION_LABELS).map(([value, label]) => ({ value, label }));

type SessionMode = SessionConceptSource;
type CookbookGroup = {
  bookId: string;
  bookTitle: string;
  recipes: DetectedRecipeCandidate[];
  chapterCount: number;
  summary: string;
};

function sortSelectionsByChunkId(selection: SelectedCookbookRecipeRef[]) {
  return [...selection].sort((a, b) => a.chunk_id.localeCompare(b.chunk_id));
}

function buildCookbookFreeText(selectedRecipes: DetectedRecipeCandidate[]): string {
  if (selectedRecipes.length === 0) {
    return 'Cookbook-selected session';
  }

  const recipeNames = selectedRecipes.map((recipe) => getRecipeDisplayTitle(recipe)).filter(Boolean);
  if (recipeNames.length === 0) {
    return 'Cookbook-selected session';
  }

  return `Cookbook-selected recipes: ${recipeNames.join(', ')}`;
}

function summarizeCookbookGroup(recipes: DetectedRecipeCandidate[]) {
  const displayTitles = recipes.map((recipe) => getRecipeDisplayTitle(recipe)).filter(Boolean);
  if (displayTitles.length === 0) {
    return 'Browse detected recipes from this cookbook.';
  }

  const summaryTitles = displayTitles.slice(0, 3);
  const remainder = displayTitles.length - summaryTitles.length;
  if (remainder <= 0) {
    return summaryTitles.join(', ');
  }

  return `${summaryTitles.join(', ')} + ${remainder} more`;
}

function groupRecipesByBook(recipes: DetectedRecipeCandidate[]): CookbookGroup[] {
  const groups = new Map<string, { bookId: string; bookTitle: string; recipes: DetectedRecipeCandidate[] }>();

  for (const recipe of recipes) {
    const existing = groups.get(recipe.book_id);
    if (existing) {
      existing.recipes.push(recipe);
      continue;
    }

    groups.set(recipe.book_id, {
      bookId: recipe.book_id,
      bookTitle: recipe.book_title,
      recipes: [recipe],
    });
  }

  return Array.from(groups.values())
    .map((group) => {
      const sortedRecipes = [...group.recipes].sort((a, b) => a.chunk_id.localeCompare(b.chunk_id));
      const chapterCount = new Set(sortedRecipes.map((recipe) => recipe.chapter).filter(Boolean)).size;

      return {
        ...group,
        recipes: sortedRecipes,
        chapterCount,
        summary: summarizeCookbookGroup(sortedRecipes),
      };
    })
    .sort((a, b) => a.bookTitle.localeCompare(b.bookTitle));
}

export function NewSessionPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<SessionMode>('free_text');
  const [freeText, setFreeText] = useState('');
  const [guestCount, setGuestCount] = useState(4);
  const [mealType, setMealType] = useState<MealType>('dinner');
  const [occasion, setOccasion] = useState<Occasion>('dinner_party');
  const [restrictions, setRestrictions] = useState<string[]>([]);
  const [restrictionInput, setRestrictionInput] = useState('');
  const [servingTime, setServingTime] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [cookbookCandidates, setCookbookCandidates] = useState<DetectedRecipeCandidate[]>([]);
  const [cookbookLoading, setCookbookLoading] = useState(false);
  const [cookbookLoaded, setCookbookLoaded] = useState(false);
  const [selectedRecipeIds, setSelectedRecipeIds] = useState<string[]>([]);
  const [expandedExcerpts, setExpandedExcerpts] = useState<Set<string>>(new Set());
  const [activeCookbookId, setActiveCookbookId] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== 'cookbook' || cookbookLoaded) {
      return;
    }

    let cancelled = false;
    setCookbookLoading(true);
    setError('');

    void listDetectedRecipes()
      .then((recipes) => {
        if (cancelled) return;
        const ordered = [...recipes].sort((a, b) => a.chunk_id.localeCompare(b.chunk_id));
        setCookbookCandidates(ordered);
        setCookbookLoaded(true);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(getErrorMessage(err, 'Could not load cookbook recipes'));
      })
      .finally(() => {
        if (!cancelled) {
          setCookbookLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [mode, cookbookLoaded]);

  const groupedRecipes = useMemo(() => groupRecipesByBook(cookbookCandidates), [cookbookCandidates]);
  const activeCookbook = useMemo(
    () => groupedRecipes.find((group) => group.bookId === activeCookbookId) ?? null,
    [activeCookbookId, groupedRecipes],
  );
  const selectedRecipes = useMemo(() => {
    const selectedSet = new Set(selectedRecipeIds);
    return cookbookCandidates.filter((recipe) => selectedSet.has(recipe.chunk_id));
  }, [cookbookCandidates, selectedRecipeIds]);

  useEffect(() => {
    if (mode !== 'cookbook') {
      setActiveCookbookId(null);
      return;
    }

    if (groupedRecipes.length === 0) {
      setActiveCookbookId(null);
      return;
    }

    if (activeCookbookId && groupedRecipes.some((group) => group.bookId === activeCookbookId)) {
      return;
    }

    setActiveCookbookId(null);
  }, [activeCookbookId, groupedRecipes, mode]);

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

  function toggleSelectedRecipe(chunkId: string) {
    setSelectedRecipeIds((prev) => {
      const exists = prev.includes(chunkId);
      const next = exists ? prev.filter((id) => id !== chunkId) : [...prev, chunkId];
      return sortSelectionsByChunkId(next.map((id) => ({ chunk_id: id }))).map((recipe) => recipe.chunk_id);
    });
  }

  const toggleExcerpt = useCallback((chunkId: string) => {
    setExpandedExcerpts((prev) => {
      const next = new Set(prev);
      if (next.has(chunkId)) {
        next.delete(chunkId);
      } else {
        next.add(chunkId);
      }
      return next;
    });
  }, []);

  function removeSelectedRecipe(chunkId: string) {
    setSelectedRecipeIds((prev) => prev.filter((id) => id !== chunkId));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const session = mode === 'cookbook'
        ? await createSession(buildCookbookRequest())
        : await createSession(buildFreeTextRequest());
      await runPipeline(session.session_id);
      navigate(`/sessions/${session.session_id}`);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Something went wrong — please try again'));
    } finally {
      setLoading(false);
    }
  }

  function buildFreeTextRequest(): CreateFreeTextSessionRequest {
    return {
      free_text: freeText,
      guest_count: guestCount,
      meal_type: mealType,
      occasion,
      dietary_restrictions: restrictions,
      serving_time: servingTime || undefined,
    };
  }

  function buildCookbookRequest(): CreateCookbookSessionRequest {
    return {
      concept_source: 'cookbook',
      free_text: buildCookbookFreeText(selectedRecipes),
      selected_recipes: sortSelectionsByChunkId(selectedRecipeIds.map((chunk_id) => ({ chunk_id }))),
      guest_count: guestCount,
      meal_type: mealType,
      occasion,
      dietary_restrictions: restrictions,
      serving_time: servingTime || undefined,
    };
  }

  const canSubmit = mode === 'cookbook' ? selectedRecipeIds.length > 0 : !!freeText.trim();
  const showCookbookChooser = mode === 'cookbook' && activeCookbook === null;

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Plan a Dinner</h1>
      <p className={styles.subtitle}>Choose between a fresh meal idea or exact recipes from your uploaded cookbooks.</p>

      <form className={styles.form} onSubmit={handleSubmit}>
        {error && <div className={styles.error}>{error}</div>}

        <div className={styles.modeSwitcher} role="radiogroup" aria-label="Session mode">
          <button
            type="button"
            className={`${styles.modeCard} ${mode === 'free_text' ? styles.modeCardActive : ''}`}
            onClick={() => setMode('free_text')}
            aria-pressed={mode === 'free_text'}
          >
            <span className={styles.modeEyebrow}>Meal idea</span>
            <span className={styles.modeTitle}>Describe the menu you want</span>
            <span className={styles.modeDescription}>Keep the classic free-text flow for a brand-new plan.</span>
          </button>
          <button
            type="button"
            className={`${styles.modeCard} ${mode === 'cookbook' ? styles.modeCardActive : ''}`}
            onClick={() => setMode('cookbook')}
            aria-pressed={mode === 'cookbook'}
          >
            <span className={styles.modeEyebrow}>Cookbook mode</span>
            <span className={styles.modeTitle}>Schedule exact uploaded recipes</span>
            <span className={styles.modeDescription}>Select detected recipes across books and run the scheduler on those exact cookbook chunks.</span>
          </button>
        </div>

        {mode === 'free_text' ? (
          <Textarea
            label="What are you cooking?"
            placeholder="A rustic Italian dinner with handmade pasta, seasonal vegetables, and something decadent for dessert..."
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            maxLength={2000}
            required
          />
        ) : (
          <section className={styles.cookbookSection} aria-labelledby="cookbook-recipes-heading">
            <div className={styles.sectionHeader}>
              <div>
                <h2 id="cookbook-recipes-heading" className={styles.sectionTitle}>
                  {showCookbookChooser ? 'Choose a cookbook to browse' : activeCookbook ? activeCookbook.bookTitle : 'Select cookbook recipes'}
                </h2>
                <p className={styles.sectionCopy}>
                  {showCookbookChooser
                    ? 'Start with a single cookbook. You can move between books without losing recipe selections, then schedule the exact chunks you picked.'
                    : 'Pick the exact recipes you want to cook from this cookbook. Your selected recipes stay pinned while you browse.'}
                </p>
              </div>
              {!showCookbookChooser && (
                <button
                  type="button"
                  className={styles.clearAllButton}
                  onClick={() => setActiveCookbookId(null)}
                  aria-label="Back to cookbook chooser"
                >
                  Back to all cookbooks
                </button>
              )}
            </div>

            {selectedRecipes.length > 0 && (
              <div className={styles.selectionSummary} aria-label="Selected recipes">
                <div className={styles.selectionSummaryHeader}>
                  <span className={styles.selectionSummaryTitle}>
                    Your menu
                    <span className={styles.selectionSummaryCount}>{selectedRecipes.length} recipe{selectedRecipes.length !== 1 ? 's' : ''}</span>
                  </span>
                  <button
                    type="button"
                    className={styles.clearAllButton}
                    onClick={() => setSelectedRecipeIds([])}
                    aria-label="Clear all selections"
                  >
                    Clear all
                  </button>
                </div>
                <div className={styles.selectionPills}>
                  {selectedRecipes.map((recipe) => {
                    const displayTitle = getRecipeDisplayTitle(recipe);
                    return (
                      <span key={recipe.chunk_id} className={styles.selectionPill}>
                        <span className={styles.selectionPillName}>{displayTitle}</span>
                        <span className={styles.selectionPillBook}>{recipe.book_title}</span>
                        <button
                          type="button"
                          className={styles.selectionPillRemove}
                          onClick={() => removeSelectedRecipe(recipe.chunk_id)}
                          aria-label={`Remove ${displayTitle}`}
                        >
                          <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                            <path d="M3 3L9 9M9 3L3 9" />
                          </svg>
                        </button>
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {cookbookLoading ? (
              <div className={styles.cookbookState}>Loading cookbook recipes…</div>
            ) : cookbookLoaded && cookbookCandidates.length === 0 ? (
              <div className={styles.cookbookState}>No detected cookbook recipes yet. Upload a cookbook and let ingestion finish first.</div>
            ) : showCookbookChooser ? (
              <div className={styles.bookGrid} role="list" aria-label="Cookbook chooser">
                {groupedRecipes.map((group) => (
                  <button
                    key={group.bookId}
                    type="button"
                    className={styles.bookCard}
                    onClick={() => setActiveCookbookId(group.bookId)}
                    aria-label={`Browse ${group.bookTitle}`}
                  >
                    <div className={styles.bookHeader}>
                      <h3 className={styles.bookTitle}>{group.bookTitle}</h3>
                      <span className={styles.bookMeta}>{group.recipes.length} recipes</span>
                    </div>
                    <p className={styles.bookSummary}>{group.summary}</p>
                    <div className={styles.recipeMetaRow}>
                      <span>{group.chapterCount} chapter{group.chapterCount !== 1 ? 's' : ''}</span>
                      <span>{group.recipes.filter((recipe) => selectedRecipeIds.includes(recipe.chunk_id)).length} selected</span>
                    </div>
                  </button>
                ))}
              </div>
            ) : activeCookbook ? (
              <div className={styles.bookGrid}>
                <section key={activeCookbook.bookId} className={styles.bookCard} aria-label={activeCookbook.bookTitle}>
                  <div className={styles.bookHeader}>
                    <h3 className={styles.bookTitle}>{activeCookbook.bookTitle}</h3>
                    <span className={styles.bookMeta}>{activeCookbook.recipes.length} recipes</span>
                  </div>
                  <p className={styles.bookSummary}>{activeCookbook.summary}</p>
                  <div className={styles.recipeList}>
                    {activeCookbook.recipes.map((recipe) => {
                      const checked = selectedRecipeIds.includes(recipe.chunk_id);
                      const isExpanded = expandedExcerpts.has(recipe.chunk_id);
                      const preview = buildCookbookCandidatePreview(recipe);
                      const excerptText = preview.excerpt;
                      const needsExpand = excerptText.length > EXCERPT_COLLAPSE_THRESHOLD || preview.ingredients.length > 0 || preview.steps.length > 0;
                      const displayTitle = getRecipeDisplayTitle(recipe);
                      return (
                        <label key={recipe.chunk_id} className={`${styles.recipeOption} ${checked ? styles.recipeOptionSelected : ''}`}>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleSelectedRecipe(recipe.chunk_id)}
                            aria-label={`Select ${displayTitle}`}
                          />
                          <span className={styles.selectionIndicator} aria-hidden="true">
                            <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M2.5 6L5 8.5L9.5 3.5" />
                            </svg>
                          </span>
                          <div className={styles.recipeOptionBody}>
                            <div className={styles.recipeOptionHeader}>
                              <span className={styles.recipeName}>{displayTitle}</span>
                              {recipe.page_number && <span className={styles.recipePage}>p. {recipe.page_number}</span>}
                            </div>
                            <div className={styles.recipeMetaRow}>
                              <span>{recipe.chapter || 'Unsorted'}</span>
                              <span className={styles.chunkId}>{recipe.chunk_id.slice(0, 8)}</span>
                            </div>
                            <div className={styles.recipePreviewSummary}>
                              {preview.ingredients.length > 0 && (
                                <span>{preview.ingredients.length} ingredients shown</span>
                              )}
                              {preview.steps.length > 0 && (
                                <span>{preview.steps.length} steps previewed</span>
                              )}
                            </div>
                            {excerptText && (
                              <div className={styles.excerptContainer}>
                                <p className={`${styles.recipeExcerpt} ${isExpanded ? styles.recipeExcerptExpanded : ''}`}>
                                  {isExpanded || !needsExpand ? excerptText : `${excerptText.slice(0, EXCERPT_COLLAPSE_THRESHOLD)}…`}
                                </p>
                                {isExpanded && preview.ingredients.length > 0 && (
                                  <div className={styles.recipePreviewBlock}>
                                    <h4 className={styles.recipePreviewHeading}>Ingredients</h4>
                                    <ul className={styles.recipePreviewList}>
                                      {preview.ingredients.map((line) => (
                                        <li key={line}>{line}</li>
                                      ))}
                                    </ul>
                                  </div>
                                )}
                                {isExpanded && preview.steps.length > 0 && (
                                  <div className={styles.recipePreviewBlock}>
                                    <h4 className={styles.recipePreviewHeading}>Method</h4>
                                    <ol className={styles.recipePreviewListOrdered}>
                                      {preview.steps.map((line) => (
                                        <li key={line}>{line}</li>
                                      ))}
                                    </ol>
                                  </div>
                                )}
                                {needsExpand && (
                                  <button
                                    type="button"
                                    className={styles.excerptToggle}
                                    onClick={(e) => {
                                      e.preventDefault();
                                      e.stopPropagation();
                                      toggleExcerpt(recipe.chunk_id);
                                    }}
                                    aria-expanded={isExpanded}
                                    aria-label={isExpanded ? 'Show less' : 'Show recipe preview'}
                                  >
                                    {isExpanded ? 'Show less' : 'Show recipe preview'}
                                  </button>
                                )}
                              </div>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </section>
              </div>
            ) : null}
          </section>
        )}

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
            {loading ? 'Starting...' : mode === 'cookbook' ? 'Schedule Selected Recipes' : 'Start Planning'}
          </Button>
          <Button type="button" variant="secondary" onClick={() => navigate('/')}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
