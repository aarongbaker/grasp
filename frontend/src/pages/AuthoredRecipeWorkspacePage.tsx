import { Link } from 'react-router-dom';
import { type FormEvent, useEffect, useMemo, useState } from 'react';
import { createAuthoredRecipe, getAuthoredRecipe } from '../api/authoredRecipes';
import { AuthoringSectionCard } from '../components/authoring/AuthoringSectionCard';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import { useAuth } from '../context/useAuth';
import { getErrorMessage } from '../utils/errors';
import type {
  AuthoredRecipeCreateRequest,
  AuthoredRecipeDetail,
  AuthoredRecipeStep,
  AuthoredRecipeYield,
  Ingredient,
  Resource,
} from '../types/api';
import styles from './AuthoredRecipeWorkspacePage.module.css';

type DraftStatus = 'new' | 'loading' | 'ready' | 'saving' | 'saved';

type IngredientDraft = Ingredient;
type StepDraft = AuthoredRecipeStep;

const resourceOptions: { value: Resource; label: string }[] = [
  { value: 'hands', label: 'Hands on the board' },
  { value: 'oven', label: 'Oven time' },
  { value: 'stovetop', label: 'Stovetop time' },
  { value: 'passive', label: 'Passive hold or rest' },
];

function createBlankIngredient(): IngredientDraft {
  return { name: '', quantity: '', preparation: '' };
}

function createBlankStep(): StepDraft {
  return {
    title: '',
    instruction: '',
    duration_minutes: 10,
    duration_max: null,
    resource: 'hands',
    required_equipment: [],
    dependencies: [],
    can_be_done_ahead: false,
    prep_ahead_window: null,
    prep_ahead_notes: null,
    target_internal_temperature_f: null,
    until_condition: null,
    yield_contribution: null,
    chef_notes: null,
  };
}

function createBlankDraft(userId: string): AuthoredRecipeCreateRequest {
  return {
    user_id: userId,
    title: '',
    description: '',
    cuisine: '',
    yield_info: {
      quantity: 4,
      unit: 'plates',
      notes: null,
    },
    ingredients: [createBlankIngredient()],
    steps: [createBlankStep()],
    equipment_notes: [],
    storage: null,
    hold: null,
    reheat: null,
    make_ahead_guidance: null,
    plating_notes: null,
    chef_notes: null,
  };
}

function hydrateDraft(recipe: AuthoredRecipeDetail): AuthoredRecipeCreateRequest {
  return {
    user_id: recipe.user_id,
    title: recipe.title,
    description: recipe.description,
    cuisine: recipe.cuisine,
    yield_info: recipe.yield_info,
    ingredients: recipe.ingredients.length > 0 ? recipe.ingredients : [createBlankIngredient()],
    steps: recipe.steps.length > 0 ? recipe.steps : [createBlankStep()],
    equipment_notes: recipe.equipment_notes,
    storage: recipe.storage,
    hold: recipe.hold,
    reheat: recipe.reheat,
    make_ahead_guidance: recipe.make_ahead_guidance,
    plating_notes: recipe.plating_notes,
    chef_notes: recipe.chef_notes,
  };
}

function buildStepDependencyOptions(steps: StepDraft[], currentIndex: number) {
  return steps
    .map((step, index) => ({
      value: index,
      label: `${index + 1}. ${step.title.trim() || `Step ${index + 1}`}`,
    }))
    .filter((option) => option.value !== currentIndex);
}

function buildStepId(title: string, index: number): string {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') || 'recipe';
  return `${slug}_step_${index + 1}`;
}

function buildSavePayload(draft: AuthoredRecipeCreateRequest): AuthoredRecipeCreateRequest {
  return {
    ...draft,
    title: draft.title.trim(),
    description: draft.description.trim(),
    cuisine: draft.cuisine.trim(),
    yield_info: {
      quantity: draft.yield_info.quantity,
      unit: draft.yield_info.unit.trim(),
      notes: draft.yield_info.notes?.trim() || null,
    },
    ingredients: draft.ingredients
      .map((ingredient) => ({
        name: ingredient.name.trim(),
        quantity: ingredient.quantity.trim(),
        preparation: ingredient.preparation.trim(),
      }))
      .filter((ingredient) => ingredient.name && ingredient.quantity),
    steps: draft.steps
      .map((step) => ({
        ...step,
        title: step.title.trim(),
        instruction: step.instruction.trim(),
        required_equipment: step.required_equipment.map((item) => item.trim()).filter(Boolean),
        dependencies: step.dependencies.map((dependency) => ({
          ...dependency,
          step_id: buildStepId(draft.title, dependency.step_id as unknown as number),
        })),
        prep_ahead_window: step.can_be_done_ahead ? step.prep_ahead_window?.trim() || null : null,
        prep_ahead_notes: step.can_be_done_ahead ? step.prep_ahead_notes?.trim() || null : null,
        until_condition: step.until_condition?.trim() || null,
        yield_contribution: step.yield_contribution?.trim() || null,
        chef_notes: step.chef_notes?.trim() || null,
        target_internal_temperature_f: step.target_internal_temperature_f || null,
        duration_max:
          step.duration_max && step.duration_max >= step.duration_minutes ? step.duration_max : null,
      }))
      .filter((step) => step.title && step.instruction),
    equipment_notes: draft.equipment_notes.map((note) => note.trim()).filter(Boolean),
    storage: draft.storage
      ? {
          method: draft.storage.method.trim(),
          duration: draft.storage.duration.trim(),
          notes: draft.storage.notes?.trim() || null,
        }
      : null,
    hold: draft.hold
      ? {
          method: draft.hold.method.trim(),
          max_duration: draft.hold.max_duration.trim(),
          notes: draft.hold.notes?.trim() || null,
        }
      : null,
    reheat: draft.reheat
      ? {
          method: draft.reheat.method.trim(),
          target: draft.reheat.target?.trim() || null,
          notes: draft.reheat.notes?.trim() || null,
        }
      : null,
    make_ahead_guidance: draft.make_ahead_guidance?.trim() || null,
    plating_notes: draft.plating_notes?.trim() || null,
    chef_notes: draft.chef_notes?.trim() || null,
  };
}

function statusCopy(status: DraftStatus, recipeId: string | null) {
  if (status === 'loading') {
    return {
      label: 'Draft status',
      value: 'Pulling your saved draft forward.',
      text: 'Loading the authored recipe before you add or refine service detail.',
    };
  }
  if (status === 'saving') {
    return {
      label: 'Draft status',
      value: 'Saving to your private recipe ledger.',
      text: 'This stores the authored contract without touching dinner-planning sessions.',
    };
  }
  if (status === 'saved' && recipeId) {
    return {
      label: 'Draft status',
      value: 'Saved as a private recipe draft.',
      text: `Saved draft ${recipeId.slice(0, 8)}… can be reopened here without starting a dinner plan.`,
    };
  }
  return {
    label: 'Draft status',
    value: 'Working draft, not yet saved.',
    text: 'Shape the dish in chef language first, then save when the draft can stand on its own.',
  };
}

export function AuthoredRecipeWorkspacePage() {
  const { userId } = useAuth();
  const [draft, setDraft] = useState<AuthoredRecipeCreateRequest | null>(null);
  const [draftStatus, setDraftStatus] = useState<DraftStatus>('new');
  const [savedRecipeId, setSavedRecipeId] = useState<string | null>(null);
  const [loadRecipeId, setLoadRecipeId] = useState('');
  const [error, setError] = useState('');
  const [activeStepIndex, setActiveStepIndex] = useState(0);
  const [showAdvanceDetails, setShowAdvanceDetails] = useState(false);

  useEffect(() => {
    if (!userId) {
      setDraft(null);
      return;
    }
    setDraft((current) => current ?? createBlankDraft(userId));
  }, [userId]);

  const currentDraft = draft;
  const currentStep = currentDraft?.steps[activeStepIndex] ?? null;
  const note = useMemo(() => statusCopy(draftStatus, savedRecipeId), [draftStatus, savedRecipeId]);

  function updateDraft(updater: (current: AuthoredRecipeCreateRequest) => AuthoredRecipeCreateRequest) {
    setDraft((current) => (current ? updater(current) : current));
    setDraftStatus((current) => (current === 'saved' ? 'ready' : current));
  }

  function updateYield<K extends keyof AuthoredRecipeYield>(key: K, value: AuthoredRecipeYield[K]) {
    updateDraft((current) => ({
      ...current,
      yield_info: {
        ...current.yield_info,
        [key]: value,
      },
    }));
  }

  function updateIngredient(index: number, patch: Partial<IngredientDraft>) {
    updateDraft((current) => ({
      ...current,
      ingredients: current.ingredients.map((ingredient, ingredientIndex) =>
        ingredientIndex === index ? { ...ingredient, ...patch } : ingredient,
      ),
    }));
  }

  function addIngredient() {
    updateDraft((current) => ({
      ...current,
      ingredients: [...current.ingredients, createBlankIngredient()],
    }));
  }

  function removeIngredient(index: number) {
    updateDraft((current) => ({
      ...current,
      ingredients:
        current.ingredients.length === 1
          ? [createBlankIngredient()]
          : current.ingredients.filter((_, ingredientIndex) => ingredientIndex !== index),
    }));
  }

  function updateStep(index: number, patch: Partial<StepDraft>) {
    updateDraft((current) => ({
      ...current,
      steps: current.steps.map((step, stepIndex) => (stepIndex === index ? { ...step, ...patch } : step)),
    }));
  }

  function addStep() {
    updateDraft((current) => ({
      ...current,
      steps: [...current.steps, createBlankStep()],
    }));
    setActiveStepIndex((currentIndex) => currentIndex + 1);
  }

  function removeStep(index: number) {
    updateDraft((current) => ({
      ...current,
      steps: current.steps.length === 1 ? [createBlankStep()] : current.steps.filter((_, i) => i !== index),
    }));
    setActiveStepIndex((currentIndex) => Math.max(0, Math.min(currentIndex, (draft?.steps.length ?? 1) - 2)));
  }

  function updateStepDependency(selectedIndex: string) {
    if (!currentDraft || !currentStep) return;
    const dependencyIndex = Number(selectedIndex);
    if (Number.isNaN(dependencyIndex) || dependencyIndex === activeStepIndex) return;

    const nextDependencyId = buildStepId(currentDraft.title, dependencyIndex);
    const dependencies = currentStep.dependencies.some((dependency) => dependency.step_id === nextDependencyId)
      ? currentStep.dependencies
      : [
          ...currentStep.dependencies,
          {
            step_id: nextDependencyId,
            kind: 'finish_to_start' as const,
            lag_minutes: 0,
          },
        ];

    updateStep(activeStepIndex, { dependencies });
  }

  function removeDependency(stepIndex: number, dependencyStepId: string) {
    const step = draft?.steps[stepIndex];
    if (!step) return;
    updateStep(stepIndex, {
      dependencies: step.dependencies.filter((dependency) => dependency.step_id !== dependencyStepId),
    });
  }

  function updateGuidance(updater: (current: AuthoredRecipeCreateRequest) => AuthoredRecipeCreateRequest) {
    updateDraft(updater);
  }

  async function handleLoadDraft(e: FormEvent) {
    e.preventDefault();
    if (!loadRecipeId.trim()) return;
    setError('');
    setDraftStatus('loading');
    try {
      const recipe = await getAuthoredRecipe(loadRecipeId.trim());
      setDraft(hydrateDraft(recipe));
      setSavedRecipeId(recipe.recipe_id);
      setDraftStatus('saved');
      setActiveStepIndex(0);
    } catch (err) {
      setDraftStatus('ready');
      setError(getErrorMessage(err, 'Could not load that recipe draft.'));
    }
  }

  async function handleSaveDraft(e: FormEvent) {
    e.preventDefault();
    if (!currentDraft || !userId) return;
    setError('');
    setDraftStatus('saving');
    try {
      const saved = await createAuthoredRecipe(buildSavePayload({ ...currentDraft, user_id: userId }));
      setDraft(hydrateDraft(saved));
      setSavedRecipeId(saved.recipe_id);
      setDraftStatus('saved');
    } catch (err) {
      setDraftStatus('ready');
      setError(getErrorMessage(err, 'Could not save this recipe draft yet.'));
    }
  }

  if (!currentDraft) {
    return null;
  }

  const dependencyOptions = buildStepDependencyOptions(currentDraft.steps, activeStepIndex);
  const draftReadyToSave =
    currentDraft.title.trim() &&
    currentDraft.description.trim() &&
    currentDraft.cuisine.trim() &&
    currentDraft.ingredients.some((ingredient) => ingredient.name.trim() && ingredient.quantity.trim()) &&
    currentDraft.steps.some((step) => step.title.trim() && step.instruction.trim());

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.kicker}>Chef-authored workspace</p>
          <h1 className={styles.title}>Open a fresh page for a dish you already know how to talk through.</h1>
          <p className={styles.subtitle}>
            Capture a private recipe draft in kitchen language: the dish itself, the prep rhythm, and what can be
            handled ahead without flattening service.
          </p>
        </div>

        <aside className={styles.heroNote} aria-label="Draft status">
          <p className={styles.noteLabel}>{note.label}</p>
          <p className={styles.noteValue}>{note.value}</p>
          <p className={styles.noteText}>{note.text}</p>
        </aside>
      </header>

      <section className={styles.callout} aria-labelledby="workspace-approach-heading">
        <div>
          <p className={styles.calloutEyebrow}>Kitchen notebook</p>
          <h2 id="workspace-approach-heading" className={styles.calloutTitle}>
            Build the draft in passes, not all at once.
          </h2>
          <p className={styles.calloutText}>
            Start with the plate, then move into ingredients, station rhythm, and hold guidance. This save seam stays
            private to authored recipes and does not start a dinner plan.
          </p>
        </div>

        <div className={styles.actions}>
          <form className={styles.loadForm} onSubmit={handleLoadDraft}>
            <Input
              label="Reopen a saved draft"
              placeholder="Paste a recipe draft ID"
              value={loadRecipeId}
              onChange={(event) => setLoadRecipeId(event.target.value)}
            />
            <Button type="submit" variant="secondary" disabled={draftStatus === 'loading' || !loadRecipeId.trim()}>
              {draftStatus === 'loading' ? 'Opening…' : 'Open saved draft'}
            </Button>
          </form>
          <Link to="/sessions/new" className={styles.secondaryLink}>
            Need to plan a full dinner instead?
          </Link>
        </div>
      </section>

      <form className={styles.workspace} onSubmit={handleSaveDraft}>
        {error ? <div className={styles.error}>{error}</div> : null}

        <section className={styles.grid} aria-label="Authoring sections">
          <AuthoringSectionCard
            eyebrow="Foundation"
            title="Name the dish and the feeling you want on the pass"
            description="Anchor the plate first, then record the portions and ingredient set that define the draft."
            prompt='"Tonight this should eat like…"'
            aside="This section keeps identity, yield, and ingredient intent together so the draft opens with what the guest meets on the plate."
          >
            <div className={styles.sectionBody}>
              <Input
                label="Dish title"
                value={currentDraft.title}
                onChange={(event) => updateDraft((current) => ({ ...current, title: event.target.value }))}
                placeholder="Charred carrots with whipped feta"
              />
              <Textarea
                label="How would you describe the dish at the pass?"
                value={currentDraft.description}
                onChange={(event) => updateDraft((current) => ({ ...current, description: event.target.value }))}
                placeholder="A warm vegetable course with smoke, acidity, and a cold dairy contrast."
                rows={4}
              />
              <div className={styles.inlineFields}>
                <Input
                  label="Cuisine or lens"
                  value={currentDraft.cuisine}
                  onChange={(event) => updateDraft((current) => ({ ...current, cuisine: event.target.value }))}
                  placeholder="Levantine"
                />
                <Input
                  label="Yield"
                  type="number"
                  min={1}
                  step="0.5"
                  value={currentDraft.yield_info.quantity}
                  onChange={(event) => updateYield('quantity', Number(event.target.value) || 0)}
                />
                <Input
                  label="Yield unit"
                  value={currentDraft.yield_info.unit}
                  onChange={(event) => updateYield('unit', event.target.value)}
                  placeholder="plates"
                />
              </div>
              <Textarea
                label="Yield note"
                value={currentDraft.yield_info.notes ?? ''}
                onChange={(event) => updateYield('notes', event.target.value || null)}
                placeholder="One composed plate with generous garnish."
                rows={2}
              />
              <div className={styles.listHeader}>
                <h3 className={styles.listTitle}>Ingredient list</h3>
                <Button type="button" variant="ghost" size="sm" onClick={addIngredient}>
                  Add ingredient
                </Button>
              </div>
              <div className={styles.stack}>
                {currentDraft.ingredients.map((ingredient, index) => (
                  <div key={`ingredient-${index}`} className={styles.cardRow}>
                    <div className={styles.inlineFields}>
                      <Input
                        label={`Ingredient ${index + 1}`}
                        value={ingredient.name}
                        onChange={(event) => updateIngredient(index, { name: event.target.value })}
                        placeholder="carrots"
                      />
                      <Input
                        label="Quantity"
                        value={ingredient.quantity}
                        onChange={(event) => updateIngredient(index, { quantity: event.target.value })}
                        placeholder="2 lb"
                      />
                    </div>
                    <div className={styles.inlineFields}>
                      <Input
                        label="Prep note"
                        value={ingredient.preparation}
                        onChange={(event) => updateIngredient(index, { preparation: event.target.value })}
                        placeholder="scrubbed, halved lengthwise"
                      />
                      <Button type="button" variant="secondary" size="sm" onClick={() => removeIngredient(index)}>
                        Remove
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </AuthoringSectionCard>

          <AuthoringSectionCard
            eyebrow="Mise en place"
            title="Sketch the prep rhythm before you worry about detail"
            description="Lay out the working beats, then open one step at a time for timing, dependencies, and station needs."
            prompt='"The work opens with…, then it tightens at…"'
            aside="The page uses step cards instead of backend field labels so timing and dependency detail stays legible in kitchen language."
          >
            <div className={styles.sectionBody}>
              <div className={styles.listHeader}>
                <h3 className={styles.listTitle}>Working beats</h3>
                <Button type="button" variant="ghost" size="sm" onClick={addStep}>
                  Add step
                </Button>
              </div>
              <div className={styles.stepRail}>
                {currentDraft.steps.map((step, index) => (
                  <button
                    key={`step-tab-${index}`}
                    type="button"
                    className={`${styles.stepTab} ${index === activeStepIndex ? styles.stepTabActive : ''}`}
                    onClick={() => setActiveStepIndex(index)}
                  >
                    <span className={styles.stepTabEyebrow}>Step {index + 1}</span>
                    <span className={styles.stepTabTitle}>{step.title.trim() || 'Untitled beat'}</span>
                  </button>
                ))}
              </div>
              {currentStep ? (
                <div className={styles.stepEditor}>
                  <div className={styles.inlineFields}>
                    <Input
                      label="Step title"
                      value={currentStep.title}
                      onChange={(event) => updateStep(activeStepIndex, { title: event.target.value })}
                      placeholder="Roast carrots"
                    />
                    <Select
                      label="Where does the work happen?"
                      options={resourceOptions}
                      value={currentStep.resource}
                      onChange={(event) => updateStep(activeStepIndex, { resource: event.target.value as Resource })}
                    />
                  </div>
                  <Textarea
                    label="What happens in this beat?"
                    value={currentStep.instruction}
                    onChange={(event) => updateStep(activeStepIndex, { instruction: event.target.value })}
                    placeholder="Roast until deeply caramelized and tender at the core."
                    rows={4}
                  />
                  <div className={styles.inlineFields}>
                    <Input
                      label="Expected minutes"
                      type="number"
                      min={1}
                      value={currentStep.duration_minutes}
                      onChange={(event) => updateStep(activeStepIndex, { duration_minutes: Number(event.target.value) || 1 })}
                    />
                    <Input
                      label="Outer edge if service drifts"
                      type="number"
                      min={currentStep.duration_minutes}
                      value={currentStep.duration_max ?? ''}
                      onChange={(event) => {
                        const next = event.target.value;
                        updateStep(activeStepIndex, { duration_max: next ? Number(next) : null });
                      }}
                    />
                    <Input
                      label="Equipment needed"
                      value={currentStep.required_equipment.join(', ')}
                      onChange={(event) =>
                        updateStep(activeStepIndex, {
                          required_equipment: event.target.value.split(',').map((item) => item.trim()).filter(Boolean),
                        })
                      }
                      placeholder="sheet tray, offset spatula"
                    />
                  </div>
                  <div className={styles.inlineFields}>
                    <Input
                      label="Until condition"
                      value={currentStep.until_condition ?? ''}
                      onChange={(event) => updateStep(activeStepIndex, { until_condition: event.target.value || null })}
                      placeholder="Edges blistered, center yielding"
                    />
                    <Input
                      label="Target internal temp"
                      type="number"
                      min={1}
                      max={500}
                      value={currentStep.target_internal_temperature_f ?? ''}
                      onChange={(event) => {
                        const next = event.target.value;
                        updateStep(activeStepIndex, {
                          target_internal_temperature_f: next ? Number(next) : null,
                        });
                      }}
                    />
                    <Input
                      label="Yield contribution"
                      value={currentStep.yield_contribution ?? ''}
                      onChange={(event) => updateStep(activeStepIndex, { yield_contribution: event.target.value || null })}
                      placeholder="Main roasted component"
                    />
                  </div>
                  <Textarea
                    label="Chef note for this beat"
                    value={currentStep.chef_notes ?? ''}
                    onChange={(event) => updateStep(activeStepIndex, { chef_notes: event.target.value || null })}
                    placeholder="Do not crowd the pan or you lose the char."
                    rows={2}
                  />
                  <div className={styles.disclosure}>
                    <label className={styles.checkboxRow}>
                      <input
                        type="checkbox"
                        checked={currentStep.can_be_done_ahead}
                        onChange={(event) =>
                          updateStep(activeStepIndex, {
                            can_be_done_ahead: event.target.checked,
                            prep_ahead_window: event.target.checked ? currentStep.prep_ahead_window : null,
                            prep_ahead_notes: event.target.checked ? currentStep.prep_ahead_notes : null,
                          })
                        }
                      />
                      <span>This beat can be handled ahead of service.</span>
                    </label>
                    {currentStep.can_be_done_ahead ? (
                      <div className={styles.inlineFields}>
                        <Input
                          label="How far ahead?"
                          value={currentStep.prep_ahead_window ?? ''}
                          onChange={(event) =>
                            updateStep(activeStepIndex, { prep_ahead_window: event.target.value || null })
                          }
                          placeholder="Up to 6 hours ahead"
                        />
                        <Input
                          label="Recovery note"
                          value={currentStep.prep_ahead_notes ?? ''}
                          onChange={(event) =>
                            updateStep(activeStepIndex, { prep_ahead_notes: event.target.value || null })
                          }
                          placeholder="Refresh with olive oil before plating"
                        />
                      </div>
                    ) : null}
                  </div>
                  <div className={styles.dependencyPanel}>
                    <div className={styles.listHeader}>
                      <h4 className={styles.subTitle}>What must finish before this beat starts?</h4>
                    </div>
                    {dependencyOptions.length > 0 ? (
                      <Select
                        label="Add a dependency"
                        options={[{ value: '', label: 'Choose a prior beat' }, ...dependencyOptions.map((option) => ({
                          value: String(option.value),
                          label: option.label,
                        }))]}
                        value=""
                        onChange={(event) => updateStepDependency(event.target.value)}
                      />
                    ) : (
                      <p className={styles.helperText}>Add another beat to link the handoff.</p>
                    )}
                    <div className={styles.dependencyList}>
                      {currentStep.dependencies.length > 0 ? (
                        currentStep.dependencies.map((dependency) => (
                          <div key={dependency.step_id} className={styles.dependencyChip}>
                            <span>{dependency.step_id}</span>
                            <button
                              type="button"
                              className={styles.inlineRemove}
                              onClick={() => removeDependency(activeStepIndex, dependency.step_id)}
                              aria-label={`Remove dependency ${dependency.step_id}`}
                            >
                              ×
                            </button>
                          </div>
                        ))
                      ) : (
                        <p className={styles.helperText}>This beat can currently open on its own.</p>
                      )}
                    </div>
                  </div>
                  <Button type="button" variant="secondary" size="sm" onClick={() => removeStep(activeStepIndex)}>
                    Remove this step
                  </Button>
                </div>
              ) : null}
            </div>
          </AuthoringSectionCard>

          <AuthoringSectionCard
            eyebrow="Advance work"
            title="Mark what can be made ahead without dulling the dish"
            description="Capture holds, storage, reheating, and plating notes in layers so the page stays readable until you need the extra detail."
            prompt='"Safe to hold if…, best refreshed by…"'
            aside="Advance-work detail stays tucked behind a single reveal so the workspace feels like a notebook, not a raw contract dump."
          >
            <div className={styles.sectionBody}>
              <label className={styles.checkboxRow}>
                <input
                  type="checkbox"
                  checked={showAdvanceDetails}
                  onChange={(event) => setShowAdvanceDetails(event.target.checked)}
                />
                <span>Open hold, storage, and recovery details.</span>
              </label>
              <Textarea
                label="Make-ahead guidance"
                value={currentDraft.make_ahead_guidance ?? ''}
                onChange={(event) =>
                  updateGuidance((current) => ({ ...current, make_ahead_guidance: event.target.value || null }))
                }
                placeholder="Roast the carrots in the afternoon, chill, then warm hard before service."
                rows={3}
              />
              {showAdvanceDetails ? (
                <div className={styles.stack}>
                  <div className={styles.cardRow}>
                    <h3 className={styles.subTitle}>Storage</h3>
                    <div className={styles.inlineFields}>
                      <Input
                        label="Method"
                        value={currentDraft.storage?.method ?? ''}
                        onChange={(event) =>
                          updateGuidance((current) => ({
                            ...current,
                            storage: {
                              method: event.target.value,
                              duration: current.storage?.duration ?? '',
                              notes: current.storage?.notes ?? null,
                            },
                          }))
                        }
                        placeholder="Refrigerated"
                      />
                      <Input
                        label="How long"
                        value={currentDraft.storage?.duration ?? ''}
                        onChange={(event) =>
                          updateGuidance((current) => ({
                            ...current,
                            storage: {
                              method: current.storage?.method ?? '',
                              duration: event.target.value,
                              notes: current.storage?.notes ?? null,
                            },
                          }))
                        }
                        placeholder="2 days"
                      />
                    </div>
                    <Textarea
                      label="Storage note"
                      value={currentDraft.storage?.notes ?? ''}
                      onChange={(event) =>
                        updateGuidance((current) => ({
                          ...current,
                          storage: {
                            method: current.storage?.method ?? '',
                            duration: current.storage?.duration ?? '',
                            notes: event.target.value || null,
                          },
                        }))
                      }
                      rows={2}
                      placeholder="Store the yogurt separately so the garnish stays fresh."
                    />
                  </div>
                  <div className={styles.cardRow}>
                    <h3 className={styles.subTitle}>Hold at service</h3>
                    <div className={styles.inlineFields}>
                      <Input
                        label="Method"
                        value={currentDraft.hold?.method ?? ''}
                        onChange={(event) =>
                          updateGuidance((current) => ({
                            ...current,
                            hold: {
                              method: event.target.value,
                              max_duration: current.hold?.max_duration ?? '',
                              notes: current.hold?.notes ?? null,
                            },
                          }))
                        }
                        placeholder="Warming drawer"
                      />
                      <Input
                        label="Longest safe hold"
                        value={currentDraft.hold?.max_duration ?? ''}
                        onChange={(event) =>
                          updateGuidance((current) => ({
                            ...current,
                            hold: {
                              method: current.hold?.method ?? '',
                              max_duration: event.target.value,
                              notes: current.hold?.notes ?? null,
                            },
                          }))
                        }
                        placeholder="15 minutes"
                      />
                    </div>
                    <Textarea
                      label="Hold note"
                      value={currentDraft.hold?.notes ?? ''}
                      onChange={(event) =>
                        updateGuidance((current) => ({
                          ...current,
                          hold: {
                            method: current.hold?.method ?? '',
                            max_duration: current.hold?.max_duration ?? '',
                            notes: event.target.value || null,
                          },
                        }))
                      }
                      rows={2}
                      placeholder="Do not cover tightly or the edges steam out."
                    />
                  </div>
                  <div className={styles.cardRow}>
                    <h3 className={styles.subTitle}>Recovery or reheat</h3>
                    <div className={styles.inlineFields}>
                      <Input
                        label="Method"
                        value={currentDraft.reheat?.method ?? ''}
                        onChange={(event) =>
                          updateGuidance((current) => ({
                            ...current,
                            reheat: {
                              method: event.target.value,
                              target: current.reheat?.target ?? null,
                              notes: current.reheat?.notes ?? null,
                            },
                          }))
                        }
                        placeholder="Hot oven"
                      />
                      <Input
                        label="Target"
                        value={currentDraft.reheat?.target ?? ''}
                        onChange={(event) =>
                          updateGuidance((current) => ({
                            ...current,
                            reheat: {
                              method: current.reheat?.method ?? '',
                              target: event.target.value || null,
                              notes: current.reheat?.notes ?? null,
                            },
                          }))
                        }
                        placeholder="Hot through, edges re-crisped"
                      />
                    </div>
                    <Textarea
                      label="Recovery note"
                      value={currentDraft.reheat?.notes ?? ''}
                      onChange={(event) =>
                        updateGuidance((current) => ({
                          ...current,
                          reheat: {
                            method: current.reheat?.method ?? '',
                            target: current.reheat?.target ?? null,
                            notes: event.target.value || null,
                          },
                        }))
                      }
                      rows={2}
                      placeholder="Brush with oil before reheating."
                    />
                  </div>
                  <Textarea
                    label="Plating note"
                    value={currentDraft.plating_notes ?? ''}
                    onChange={(event) => updateGuidance((current) => ({ ...current, plating_notes: event.target.value || null }))}
                    placeholder="Swipe the feta first, then stack the carrots for height."
                    rows={2}
                  />
                  <Textarea
                    label="Whole-dish chef note"
                    value={currentDraft.chef_notes ?? ''}
                    onChange={(event) => updateGuidance((current) => ({ ...current, chef_notes: event.target.value || null }))}
                    placeholder="Best when the yogurt stays very cold against the hot vegetables."
                    rows={2}
                  />
                  <Textarea
                    label="Equipment note"
                    value={currentDraft.equipment_notes.join('\n')}
                    onChange={(event) =>
                      updateGuidance((current) => ({
                        ...current,
                        equipment_notes: event.target.value
                          .split('\n')
                          .map((line) => line.trim())
                          .filter(Boolean),
                      }))
                    }
                    placeholder="Needs one full sheet tray.\nKeep a warm platter near the pass."
                    rows={3}
                  />
                </div>
              ) : (
                <p className={styles.helperText}>Open the detail section when you want to record holds, storage, and recovery notes.</p>
              )}
            </div>
          </AuthoringSectionCard>
        </section>

        <section className={styles.footerNote} aria-labelledby="separation-heading">
          <h2 id="separation-heading" className={styles.footerTitle}>
            Keep authored drafting and menu planning in their own lanes.
          </h2>
          <p className={styles.footerText}>
            Use <span className={styles.emphasis}>Plan a Dinner</span> when you are building service around a menu idea.
            Use this workspace when you already have a dish in mind and want a private structured recipe draft.
          </p>
          <div className={styles.footerActions}>
            <Button type="submit" size="lg" disabled={draftStatus === 'saving' || !draftReadyToSave}>
              {draftStatus === 'saving' ? 'Saving draft…' : 'Save private recipe draft'}
            </Button>
            <Link to="/sessions/new" className={styles.secondaryLink}>
              Dinner planning stays in the separate planner.
            </Link>
          </div>
        </section>
      </form>
    </div>
  );
}
