import { type FormEvent, type KeyboardEvent, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  confirmGenerationPaymentMethod,
  createGenerationSetupSession,
  getGenerationPaymentMethodStatus,
} from '../api/billing';
import { createSession, resolvePlannerReference, runPipeline } from '../api/sessions';
import { pathwayByKey } from '../components/layout/pathways';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import {
  MEAL_TYPE_LABELS,
  OCCASION_LABELS,
  PLANNER_COOKBOOK_MODE_LABELS,
  type BillingSetupSessionResponse,
  type CatalogAccessDiagnostics,
  type CreateFreeTextSessionRequest,
  type CreatePlannerAuthoredAnchorSessionRequest,
  type CreatePlannerCatalogCookbookSessionRequest,
  type CreatePlannerCookbookTargetSessionRequest,
  type CreateSessionRequest,
  type MealType,
  type Occasion,
  type PlannerAuthoredResolutionMatch,
  type PlannerCatalogCookbookReference,
  type PlannerCookbookPlanningMode,
  type PlannerCookbookResolutionMatch,
  type PlannerReferenceKind,
  type PlannerReferenceResolutionResponse,
  type PlannerResolutionMatchStatus,
  type SessionRunBlockedResponse,
} from '../types/api';
import { getErrorMessage, getSessionRunBlockedDetail } from '../utils/errors';
import styles from './NewSessionPage.module.css';

const mealTypeOptions = Object.entries(MEAL_TYPE_LABELS).map(([value, label]) => ({ value, label }));
const occasionOptions = Object.entries(OCCASION_LABELS).map(([value, label]) => ({ value, label }));

const plannerAnchorOptions = [
  { value: 'none', label: 'Menu intent only' },
  { value: 'authored', label: 'Saved recipe anchor' },
  { value: 'cookbook', label: 'Cookbook target' },
] as const;

const plannerCookbookModeOptions = Object.entries(PLANNER_COOKBOOK_MODE_LABELS).map(([value, label]) => ({
  value,
  label,
}));

type PlannerAnchorMode = (typeof plannerAnchorOptions)[number]['value'];
type CatalogHandoffStatus = 'missing' | 'invalid' | 'included' | 'preview' | 'locked';
type PlannerResolutionPhase = 'idle' | 'resolving' | 'resolved' | 'error';

interface PlannerCatalogLocationState {
  plannerCatalogCookbook?: PlannerCatalogCookbookReference;
}

interface PlannerResolutionState {
  phase: PlannerResolutionPhase;
  status: PlannerResolutionMatchStatus | null;
  query: string;
  response: PlannerReferenceResolutionResponse | null;
  error: string;
  selectedMatchId: string;
}

interface BillingSetupState {
  blockedRun: SessionRunBlockedResponse | null;
  setupSession: BillingSetupSessionResponse | null;
  loading: boolean;
  confirming: boolean;
  error: string;
}

interface CatalogPlannerRemediation {
  eyebrow: string;
  headline: string;
  body: string;
  hint: string;
  links: Array<{ label: string; to: string }>;
}

const idleResolutionState: PlannerResolutionState = {
  phase: 'idle',
  status: null,
  query: '',
  response: null,
  error: '',
  selectedMatchId: '',
};

const idleBillingSetupState: BillingSetupState = {
  blockedRun: null,
  setupSession: null,
  loading: false,
  confirming: false,
  error: '',
};

function getPlannerMatchId(match: PlannerAuthoredResolutionMatch | PlannerCookbookResolutionMatch): string {
  return match.kind === 'authored' ? match.recipe_id : match.cookbook_id;
}

function isCatalogAccessDiagnostics(value: unknown): value is CatalogAccessDiagnostics {
  if (value == null) {
    return true;
  }
  if (!value || typeof value !== 'object') {
    return false;
  }

  const diagnostics = value as Partial<CatalogAccessDiagnostics>;
  return (
    (diagnostics.subscription_snapshot_id == null || typeof diagnostics.subscription_snapshot_id === 'string') &&
    (diagnostics.subscription_status == null || typeof diagnostics.subscription_status === 'string') &&
    (diagnostics.sync_state == null || typeof diagnostics.sync_state === 'string') &&
    (diagnostics.provider == null || typeof diagnostics.provider === 'string')
  );
}

function isPlannerCatalogCookbookReference(value: unknown): value is PlannerCatalogCookbookReference {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const item = value as Partial<PlannerCatalogCookbookReference>;
  return (
    typeof item.catalog_cookbook_id === 'string' &&
    item.catalog_cookbook_id.length > 0 &&
    typeof item.slug === 'string' &&
    typeof item.title === 'string' &&
    (item.access_state === 'included' || item.access_state === 'preview' || item.access_state === 'locked') &&
    typeof item.access_state_reason === 'string' &&
    isCatalogAccessDiagnostics(item.access_diagnostics)
  );
}

function getCatalogHandoffStatus(value: unknown): CatalogHandoffStatus {
  if (value == null) {
    return 'missing';
  }
  if (!isPlannerCatalogCookbookReference(value)) {
    return 'invalid';
  }
  return value.access_state;
}

function getCatalogPlannerRemediation(
  status: CatalogHandoffStatus,
  plannerCatalogCookbook: PlannerCatalogCookbookReference | null,
  links: {
    catalog: { label: string; to: string };
    profile: { label: string; to: string };
  },
): CatalogPlannerRemediation | null {
  if (status === 'missing') {
    return null;
  }

  if (status === 'invalid') {
    return {
      eyebrow: 'Catalog handoff invalid',
      headline: 'The catalog selection could not be trusted in this planner state.',
      body: 'The handoff shape was malformed, so the planner will not submit a catalog-backed session from this page.',
      hint: 'Return to the catalog detail page and reopen the planner from a valid cookbook detail handoff.',
      links: [links.catalog],
    };
  }

  if (!plannerCatalogCookbook) {
    return null;
  }

  const syncFailed = plannerCatalogCookbook.access_diagnostics?.sync_state === 'failed';

  if (status === 'included') {
    return {
      eyebrow: 'Catalog cookbook included',
      headline: plannerCatalogCookbook.title,
      body: 'This catalog cookbook was handed off from the platform catalog and will seed the planner through the catalog access lane rather than your private cookbook folders.',
      hint: plannerCatalogCookbook.access_state_reason,
      links: [links.catalog],
    };
  }

  if (status === 'preview') {
    return {
      eyebrow: 'Catalog cookbook preview',
      headline: plannerCatalogCookbook.title,
      body: 'Preview access is valid for planning. The planner will seed one catalog anchor and keep catalog wording separate from your private cookbook shelves.',
      hint: `${plannerCatalogCookbook.access_state_reason} If you need more catalog access after this preview, review account access from your profile.`,
      links: [links.catalog, links.profile],
    };
  }

  if (syncFailed) {
    return {
      eyebrow: 'Catalog access refresh needed',
      headline: plannerCatalogCookbook.title,
      body: 'This catalog handoff is temporarily blocked while your account access refresh finishes, so session creation stays disabled here until backend access settles.',
      hint: `${plannerCatalogCookbook.access_state_reason} Review account access first, then return to the catalog when the refresh is complete.`,
      links: [links.profile, links.catalog],
    };
  }

  return {
    eyebrow: 'Catalog cookbook locked',
    headline: plannerCatalogCookbook.title,
    body: 'This handoff is blocked by the backend-provided catalog access state, so session creation stays disabled here.',
    hint: `${plannerCatalogCookbook.access_state_reason} Review account access to unlock more catalog access, or return to the catalog and choose an included or preview cookbook.`,
    links: [links.profile, links.catalog],
  };
}

export function NewSessionPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [freeText, setFreeText] = useState('');
  const [guestCount, setGuestCount] = useState(4);
  const [dishCount, setDishCount] = useState(3);
  const [mealType, setMealType] = useState<MealType>('dinner');
  const [occasion, setOccasion] = useState<Occasion>('dinner_party');
  const [restrictions, setRestrictions] = useState<string[]>([]);
  const [restrictionInput, setRestrictionInput] = useState('');
  const [servingTime, setServingTime] = useState('');
  const [plannerAnchorMode, setPlannerAnchorMode] = useState<PlannerAnchorMode>('none');
  const [plannerReferenceInput, setPlannerReferenceInput] = useState('');
  const [plannerResolution, setPlannerResolution] = useState<PlannerResolutionState>(idleResolutionState);
  const [plannerCookbookMode, setPlannerCookbookMode] = useState<PlannerCookbookPlanningMode | ''>('');
  const catalogHandoffCandidate = (location.state as PlannerCatalogLocationState | null)?.plannerCatalogCookbook;
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [billingSetup, setBillingSetup] = useState<BillingSetupState>(idleBillingSetupState);

  const recipeLibrary = pathwayByKey['recipe-library'];
  const catalogPathway = pathwayByKey.catalog;
  const authoredWorkspace = pathwayByKey['authored-workspace'];
  const profilePath = '/profile';
  const crossLinks = useMemo(
    () => [
      {
        label: catalogPathway.title,
        to: catalogPathway.to,
        description: 'Browse the platform catalog when you want preview, included, or locked cookbook access states before choosing what to cook from.',
      },
      {
        label: recipeLibrary.title,
        to: recipeLibrary.to,
        description: 'Reach for the private library when the dish already exists and you want to reopen or schedule your own saved work.',
      },
      {
        label: authoredWorkspace.title,
        to: authoredWorkspace.to,
        description: 'Use the workspace first when you need to draft the dish itself before it belongs in service planning.',
      },
    ],
    [authoredWorkspace.title, authoredWorkspace.to, catalogPathway.title, catalogPathway.to, recipeLibrary.title, recipeLibrary.to],
  );

  const plannerAnchorDescription =
    plannerAnchorMode === 'authored'
      ? 'Name the owned recipe you mean, resolve it inline, and keep the dinner brief anchored there.'
      : plannerAnchorMode === 'cookbook'
        ? 'Name the owned cookbook you mean, resolve it inline, and choose how tightly the planner should stay inside it.'
        : 'Start from menu intent alone when you want the planner to build the dinner from a clean brief.';

  const catalogHandoffStatus = useMemo(() => getCatalogHandoffStatus(catalogHandoffCandidate), [catalogHandoffCandidate]);
  const plannerCatalogCookbook = useMemo(
    () => (isPlannerCatalogCookbookReference(catalogHandoffCandidate) ? catalogHandoffCandidate : null),
    [catalogHandoffCandidate],
  );
  const catalogPlannerRemediation = useMemo(
    () =>
      getCatalogPlannerRemediation(catalogHandoffStatus, plannerCatalogCookbook, {
        catalog: { label: 'Back to catalog', to: catalogPathway.to },
        profile: { label: 'Review account access', to: profilePath },
      }),
    [catalogHandoffStatus, plannerCatalogCookbook, catalogPathway.to, profilePath],
  );

  const selectedPlannerMatch = useMemo(() => {
    if (!plannerResolution.response) {
      return null;
    }
    return (
      plannerResolution.response.matches.find((match) => getPlannerMatchId(match) === plannerResolution.selectedMatchId) ??
      null
    );
  }, [plannerResolution.response, plannerResolution.selectedMatchId]);

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

  function resetPlannerResolution() {
    setPlannerResolution(idleResolutionState);
    setPlannerCookbookMode('');
  }

  function clearBillingSetup() {
    setBillingSetup(idleBillingSetupState);
  }

  function handlePlannerAnchorModeChange(value: PlannerAnchorMode) {
    setPlannerAnchorMode(value);
    setPlannerReferenceInput('');
    resetPlannerResolution();
    setError('');
    clearBillingSetup();
  }

  function handlePlannerReferenceInputChange(value: string) {
    setPlannerReferenceInput(value);
    setError('');
    clearBillingSetup();
    setPlannerResolution((current) => {
      if (current.phase === 'idle' && current.query === '') {
        return current;
      }
      return {
        ...idleResolutionState,
      };
    });
    if (plannerAnchorMode !== 'cookbook') {
      setPlannerCookbookMode('');
    }
  }

  async function handleResolvePlannerReference() {
    if (plannerAnchorMode === 'none') {
      return;
    }

    const reference = plannerReferenceInput.trim();
    if (!reference) {
      setPlannerResolution({
        ...idleResolutionState,
        phase: 'error',
        error: plannerAnchorMode === 'authored' ? 'Enter a saved recipe name to resolve it.' : 'Enter a cookbook name to resolve it.',
      });
      return;
    }

    const kind: PlannerReferenceKind = plannerAnchorMode === 'authored' ? 'authored' : 'cookbook';
    setPlannerResolution({
      phase: 'resolving',
      status: null,
      query: reference,
      response: null,
      error: '',
      selectedMatchId: '',
    });

    try {
      const response = await resolvePlannerReference({ kind, reference });
      setPlannerResolution({
        phase: 'resolved',
        status: response.status,
        query: response.reference,
        response,
        error: '',
        selectedMatchId: response.status === 'resolved' && response.matches[0] ? getPlannerMatchId(response.matches[0]) : '',
      });
    } catch (err: unknown) {
      setPlannerResolution({
        phase: 'error',
        status: null,
        query: reference,
        response: null,
        error: getErrorMessage(err, 'Could not resolve that planner reference right now.'),
        selectedMatchId: '',
      });
    }
  }

  function buildRequest(): CreateSessionRequest {
    const sharedFields = {
      free_text: freeText,
      guest_count: guestCount,
      dish_count: dishCount,
      meal_type: mealType,
      occasion,
      dietary_restrictions: restrictions,
      serving_time: servingTime || undefined,
    };

    if (plannerCatalogCookbook) {
      if (catalogHandoffStatus === 'locked') {
        throw new Error('This catalog cookbook is locked. Return to the catalog and choose an included or preview cookbook before starting the plan.');
      }
      if (catalogHandoffStatus === 'invalid') {
        throw new Error('The catalog cookbook handoff was malformed. Return to the catalog detail page and reopen the planner from there.');
      }

      const request: CreatePlannerCatalogCookbookSessionRequest = {
        concept_source: 'planner_catalog_cookbook',
        ...sharedFields,
        planner_catalog_cookbook: {
          catalog_cookbook_id: plannerCatalogCookbook.catalog_cookbook_id,
        },
      };
      return request;
    }

    if (catalogHandoffStatus === 'invalid') {
      throw new Error('The catalog cookbook handoff was malformed. Return to the catalog detail page and reopen the planner from there.');
    }

    if (plannerAnchorMode === 'authored') {
      if (!plannerResolution.response || plannerResolution.response.status === 'no_match') {
        throw new Error('Resolve the saved recipe reference before starting the plan.');
      }
      if (!selectedPlannerMatch || selectedPlannerMatch.kind !== 'authored') {
        throw new Error('Choose the intended saved recipe before starting the plan.');
      }

      const request: CreatePlannerAuthoredAnchorSessionRequest = {
        concept_source: 'planner_authored_anchor',
        ...sharedFields,
        planner_authored_recipe_anchor: {
          recipe_id: selectedPlannerMatch.recipe_id,
          title: selectedPlannerMatch.title,
        },
      };
      return request;
    }

    if (plannerAnchorMode === 'cookbook') {
      if (!plannerResolution.response || plannerResolution.response.status === 'no_match') {
        throw new Error('Resolve the cookbook reference before starting the plan.');
      }
      if (!selectedPlannerMatch || selectedPlannerMatch.kind !== 'cookbook') {
        throw new Error('Choose the intended cookbook before starting the plan.');
      }
      if (!plannerCookbookMode) {
        throw new Error('Choose how tightly the planner should follow that cookbook before starting the plan.');
      }

      const request: CreatePlannerCookbookTargetSessionRequest = {
        concept_source: 'planner_cookbook_target',
        ...sharedFields,
        planner_cookbook_target: {
          cookbook_id: selectedPlannerMatch.cookbook_id,
          name: selectedPlannerMatch.name,
          mode: plannerCookbookMode,
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

  async function resumeBlockedRun(blockedRun: SessionRunBlockedResponse) {
    const resumedRun = await runPipeline(blockedRun.session_id);
    if (resumedRun.status !== 'generating') {
      throw new Error('The session is still waiting on payment setup. Finish saving a card, then try again.');
    }
    clearBillingSetup();
    navigate(`/sessions/${blockedRun.session_id}`);
  }

  async function handleOpenPaymentSetup() {
    if (!billingSetup.blockedRun || billingSetup.loading) {
      return;
    }

    setBillingSetup((current) => ({
      ...current,
      loading: true,
      error: '',
    }));

    try {
      const setupSession = await createGenerationSetupSession(billingSetup.blockedRun.session_id);
      window.open(setupSession.url, '_blank', 'noopener,noreferrer');
      setBillingSetup((current) => ({
        ...current,
        setupSession,
        loading: false,
      }));
    } catch (err: unknown) {
      setBillingSetup((current) => ({
        ...current,
        loading: false,
        error: getErrorMessage(err, 'Could not open payment setup right now.'),
      }));
    }
  }

  async function handleConfirmPaymentSetup() {
    if (!billingSetup.blockedRun || billingSetup.confirming) {
      return;
    }

    setBillingSetup((current) => ({
      ...current,
      confirming: true,
      error: '',
    }));

    try {
      const confirmedStatus = await confirmGenerationPaymentMethod();
      if (!confirmedStatus.has_saved_payment_method) {
        const refreshedStatus = await getGenerationPaymentMethodStatus();
        if (!refreshedStatus.has_saved_payment_method) {
          throw new Error('We still cannot confirm a saved card for this account. Finish setup, then try again.');
        }
      }
      await resumeBlockedRun(billingSetup.blockedRun);
    } catch (err: unknown) {
      setBillingSetup((current) => ({
        ...current,
        confirming: false,
        error: getErrorMessage(err, 'Could not confirm the saved card yet.'),
      }));
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (loading) {
      return;
    }

    setError('');
    clearBillingSetup();

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
      const runResponse = await runPipeline(session.session_id);
      if (runResponse.status === 'blocked') {
        setBillingSetup({
          blockedRun: runResponse,
          setupSession: null,
          loading: false,
          confirming: false,
          error: '',
        });
        return;
      }
      navigate(`/sessions/${session.session_id}`);
    } catch (err: unknown) {
      const blockedRun = getSessionRunBlockedDetail(err);
      if (blockedRun) {
        setBillingSetup({
          blockedRun,
          setupSession: null,
          loading: false,
          confirming: false,
          error: '',
        });
        return;
      }
      setError(getErrorMessage(err, 'Something went wrong — please try again'));
    } finally {
      setLoading(false);
    }
  }

  const canSubmit = !!freeText.trim() && catalogHandoffStatus !== 'locked' && catalogHandoffStatus !== 'invalid';
  const isPlannerResolutionBusy = plannerResolution.phase === 'resolving';
  const isCookbookModeResolved = plannerResolution.response?.kind === 'cookbook';
  const isBillingBlocked = billingSetup.blockedRun !== null;

  return (
    <div className={styles.page}>
      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.hero}>
          <div className={styles.heroMain}>
            <div>
              <h1 className={styles.title}>Plan a Dinner</h1>
              <p className={styles.subtitle}>
                Describe the meal you want to cook from a fresh dinner brief, a platform catalog cookbook handoff, or one owned recipe or cookbook folder.
                GRASP will turn that menu intent into a paced dinner service with timing, equipment flow, and a finished schedule.
              </p>
            </div>

            {error && <div className={styles.error}>{error}</div>}
            {isBillingBlocked && billingSetup.blockedRun && (
              <div className={styles.billingCard} role="status" aria-live="polite">
                <p className={styles.resultEyebrow}>Saved card required</p>
                <p className={styles.resultHeadline}>Finish payment setup to keep this same session moving.</p>
                <p className={styles.resultText}>{billingSetup.blockedRun.message}</p>
                <p className={styles.recoveryHint}>
                  Your dinner brief is already saved on this session. Add a payment method in the secure window, then confirm here to resume generation without recreating the plan.
                </p>
                {billingSetup.setupSession && (
                  <p className={styles.billingMeta}>
                    Secure setup opened in a new tab. Return here after you finish saving the card.
                  </p>
                )}
                {billingSetup.error && <p className={styles.billingError}>{billingSetup.error}</p>}
                <div className={styles.billingActions}>
                  <Button
                    type="button"
                    onClick={() => void handleOpenPaymentSetup()}
                    disabled={billingSetup.loading || billingSetup.confirming}
                  >
                    {billingSetup.loading ? 'Opening secure setup…' : 'Add payment method'}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => void handleConfirmPaymentSetup()}
                    disabled={billingSetup.loading || billingSetup.confirming}
                  >
                    {billingSetup.confirming ? 'Resuming session…' : 'I added a card — resume this session'}
                  </Button>
                </div>
              </div>
            )}

            <Textarea
              label="What are you cooking?"
              placeholder="A rustic Italian dinner with handmade pasta, seasonal vegetables, and something decadent for dessert..."
              value={freeText}
              onChange={(e) => {
                setFreeText(e.target.value);
                if (isBillingBlocked) {
                  clearBillingSetup();
                }
              }}
              maxLength={2000}
              required
            />
          </div>

          <aside className={styles.guidanceCard} aria-labelledby="planner-lane-heading">
            <p className={styles.guidanceEyebrow}>Planner lane</p>
            <h2 id="planner-lane-heading" className={styles.guidanceTitle}>
              Start here when service timing leads.
            </h2>
            <p className={styles.guidanceText}>
              Keep this route for menu-intent planning. It stays focused on a single dinner brief and does not switch into
              platform catalog browsing, private-library browsing, or authored drafting.
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

          {catalogPlannerRemediation && (
            <div className={styles.catalogLaneCard} aria-live="polite">
              <p className={styles.resultEyebrow}>{catalogPlannerRemediation.eyebrow}</p>
              <p className={styles.resultHeadline}>{catalogPlannerRemediation.headline}</p>
              <p className={styles.resultText}>{catalogPlannerRemediation.body}</p>
              <p className={styles.recoveryHint}>{catalogPlannerRemediation.hint}</p>
              <div className={styles.catalogLaneLinks}>
                {catalogPlannerRemediation.links.map((link) => (
                  <Link key={link.to} to={link.to} className={styles.catalogLaneLink}>
                    {link.label}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {!plannerCatalogCookbook && (
            <div className={styles.anchorGrid}>
              <Select
                label="Planner anchor"
                options={plannerAnchorOptions.map((option) => ({ value: option.value, label: option.label }))}
                value={plannerAnchorMode}
                onChange={(e) => handlePlannerAnchorModeChange(e.target.value as PlannerAnchorMode)}
              />

              {plannerAnchorMode !== 'none' && (
                <div className={styles.referenceInputRow}>
                  <Input
                    label={plannerAnchorMode === 'authored' ? 'Saved recipe reference' : 'Cookbook reference'}
                    placeholder={
                      plannerAnchorMode === 'authored'
                        ? 'Type the owned recipe name the planner should anchor to'
                        : 'Type the owned cookbook name the planner should target'
                    }
                    value={plannerReferenceInput}
                    onChange={(e) => handlePlannerReferenceInputChange(e.target.value)}
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    className={styles.resolveButton}
                    onClick={() => void handleResolvePlannerReference()}
                    disabled={isPlannerResolutionBusy}
                  >
                    {isPlannerResolutionBusy ? 'Resolving…' : 'Resolve'}
                  </Button>
                </div>
              )}
            </div>
          )}

          <div className={styles.anchorMeta}>
            <span className={styles.anchorMetaLabel}>
              {plannerCatalogCookbook
                ? catalogHandoffStatus === 'locked'
                  ? plannerCatalogCookbook.access_diagnostics?.sync_state === 'failed'
                    ? 'The backend kept this catalog cookbook out of planning because account access is still refreshing, so planner submission remains disabled until that state recovers.'
                    : 'The backend marked this catalog cookbook as locked, so planner submission is intentionally blocked until you choose another catalog cookbook.'
                  : catalogHandoffStatus === 'preview'
                    ? 'Preview catalog access is authoritative here. The planner will submit the catalog-backed payload only while this handoff remains valid.'
                    : 'Included catalog access is authoritative here. The planner will submit the catalog-backed payload without reusing the private cookbook target lane.'
                : plannerAnchorMode === 'none'
                  ? 'No owned reference is required unless you want the planner anchored to one saved recipe or cookbook.'
                  : plannerAnchorMode === 'authored'
                    ? 'Resolve one owned recipe title inline so no-match, ambiguity, and retry states stay visible before session creation.'
                    : 'Resolve one owned cookbook inline, then choose whether planning stays strict to that shelf or only leans toward it.'}
            </span>
            {plannerCatalogCookbook && (
              <span className={styles.anchorMetaDetail}>
                {catalogHandoffStatus === 'included'
                  ? `Catalog lane ready with “${plannerCatalogCookbook.title}”.`
                  : catalogHandoffStatus === 'preview'
                    ? `Catalog preview lane ready with “${plannerCatalogCookbook.title}”.`
                    : catalogHandoffStatus === 'locked'
                      ? plannerCatalogCookbook.access_diagnostics?.sync_state === 'failed'
                        ? `Catalog lane waiting on refreshed account access for “${plannerCatalogCookbook.title}”.`
                        : `Catalog lane blocked for “${plannerCatalogCookbook.title}”.`
                      : 'Catalog lane is waiting for a valid handoff.'}
              </span>
            )}
            {plannerResolution.phase === 'resolved' && plannerResolution.status === 'resolved' && selectedPlannerMatch && (
              <span className={styles.anchorMetaDetail}>
                {selectedPlannerMatch.kind === 'authored'
                  ? `Planner anchor set to “${selectedPlannerMatch.title}”.`
                  : `Planner target set to “${selectedPlannerMatch.name}”.`}
              </span>
            )}
          </div>

          {!plannerCatalogCookbook && plannerAnchorMode !== 'none' && (
            <div className={styles.resolutionPanel} aria-live="polite">
              {plannerResolution.phase === 'error' && (
                <div className={styles.inlineError}>
                  <p className={styles.resultEyebrow}>Resolve unavailable</p>
                  <p className={styles.resultHeadline}>The planner could not confirm that owned reference right now.</p>
                  <p className={styles.resultText}>{plannerResolution.error}</p>
                  <p className={styles.recoveryHint}>
                    Keep the dinner brief here, adjust the reference if needed, and resolve again when the library is reachable.
                  </p>
                </div>
              )}

              {plannerResolution.phase === 'resolved' && plannerResolution.status === 'no_match' && (
                <div className={styles.inlineNotice}>
                  <p className={styles.resultEyebrow}>No owned match</p>
                  <p className={styles.resultHeadline}>
                    Nothing in your {plannerAnchorMode === 'authored' ? 'saved recipes' : 'cookbooks'} matched “
                    {plannerResolution.query}”.
                  </p>
                  <p className={styles.resultText}>
                    Correct the {plannerAnchorMode === 'authored' ? 'recipe title' : 'cookbook name'} and resolve again. The
                    planner stays in this lane, but it will not start until one owned reference resolves.
                  </p>
                </div>
              )}

              {plannerResolution.phase === 'resolved' && plannerResolution.status === 'resolved' && selectedPlannerMatch && (
                <div className={styles.resultCard}>
                  <p className={styles.resultEyebrow}>Owned match resolved</p>
                  {selectedPlannerMatch.kind === 'authored' ? (
                    <>
                      <p className={styles.resultHeadline}>{selectedPlannerMatch.title}</p>
                      <p className={styles.resultText}>
                        This saved recipe is the owned anchor. GRASP will plan the rest of the dinner around it while the
                        remaining dishes stay open to generation.
                      </p>
                    </>
                  ) : (
                    <>
                      <p className={styles.resultHeadline}>{selectedPlannerMatch.name}</p>
                      <p className={styles.resultText}>
                        {selectedPlannerMatch.description || 'This owned cookbook folder is the planner target for the dinner brief.'}
                      </p>
                    </>
                  )}
                </div>
              )}

              {plannerResolution.phase === 'resolved' && plannerResolution.status === 'ambiguous' && plannerResolution.response && (
                <div className={styles.resultCard}>
                  <p className={styles.resultEyebrow}>Multiple owned matches</p>
                  <p className={styles.resultHeadline}>Choose the exact {plannerAnchorMode === 'authored' ? 'recipe' : 'cookbook'} before starting the planner.</p>
                  <p className={styles.resultText}>
                    “{plannerResolution.query}” matched more than one owned {plannerAnchorMode === 'authored' ? 'recipe' : 'cookbook'}.
                    The planner stays blocked in this lane until you choose one exact match.
                  </p>
                  <p className={styles.recoveryHint}>
                    Review the owned matches below, choose the one you mean, then continue with this dinner brief.
                  </p>
                  <div className={styles.choiceList} role="radiogroup" aria-label="Planner reference matches">
                    {plannerResolution.response.matches.map((match) => {
                      const matchId = getPlannerMatchId(match);
                      const checked = plannerResolution.selectedMatchId === matchId;
                      return (
                        <label key={matchId} className={styles.choiceCard}>
                          <input
                            type="radio"
                            name="planner-reference-match"
                            value={matchId}
                            checked={checked}
                            onChange={() =>
                              setPlannerResolution((current) => ({
                                ...current,
                                selectedMatchId: matchId,
                              }))
                            }
                          />
                          <span className={styles.choiceBody}>
                            <span className={styles.choiceTitle}>{match.kind === 'authored' ? match.title : match.name}</span>
                            <span className={styles.choiceDescription}>
                              {match.kind === 'authored'
                                ? 'Saved recipe anchor'
                                : match.description || 'Cookbook folder'}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}

              {plannerAnchorMode === 'cookbook' && isCookbookModeResolved && (
                <div className={styles.modeCard}>
                  <p className={styles.resultEyebrow}>Cookbook planning mode</p>
                  <p className={styles.resultText}>
                    Decide whether this dinner must stay inside the chosen cookbook folder or whether the planner can borrow outside dishes while still leaning on that owned collection.
                  </p>
                  <p className={styles.recoveryHint}>
                    The planner remains blocked until you pick one mode, so the target cookbook guidance is explicit before session creation.
                  </p>
                  <Select
                    label="Cookbook planning mode"
                    options={[{ value: '', label: 'Choose a planning mode' }, ...plannerCookbookModeOptions]}
                    value={plannerCookbookMode}
                    onChange={(e) => setPlannerCookbookMode(e.target.value as PlannerCookbookPlanningMode | '')}
                  />
                </div>
              )}
            </div>
          )}
        </section>

        <div className={styles.row}>
          <Input
            label="Guests"
            type="number"
            min={1}
            max={100}
            value={guestCount}
            onChange={(e) => setGuestCount(Math.min(100, Math.max(1, Number(e.target.value) || 1)))}
          />
          <Input
            label="Dishes"
            type="number"
            min={1}
            max={12}
            value={dishCount}
            onChange={(e) => setDishCount(Math.min(12, Math.max(1, Number(e.target.value) || 1)))}
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
          <Button type="submit" disabled={loading || !canSubmit || billingSetup.confirming}>
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
