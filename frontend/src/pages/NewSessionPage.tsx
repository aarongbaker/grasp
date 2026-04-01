import { type FormEvent, type KeyboardEvent, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { createSession, runPipeline } from '../api/sessions';
import { pathwayByKey } from '../components/layout/pathways';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import {
  MEAL_TYPE_LABELS,
  OCCASION_LABELS,
  type CreateFreeTextSessionRequest,
  type MealType,
  type Occasion,
} from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './NewSessionPage.module.css';

const mealTypeOptions = Object.entries(MEAL_TYPE_LABELS).map(([value, label]) => ({ value, label }));
const occasionOptions = Object.entries(OCCASION_LABELS).map(([value, label]) => ({ value, label }));

export function NewSessionPage() {
  const navigate = useNavigate();
  const [freeText, setFreeText] = useState('');
  const [guestCount, setGuestCount] = useState(4);
  const [mealType, setMealType] = useState<MealType>('dinner');
  const [occasion, setOccasion] = useState<Occasion>('dinner_party');
  const [restrictions, setRestrictions] = useState<string[]>([]);
  const [restrictionInput, setRestrictionInput] = useState('');
  const [servingTime, setServingTime] = useState('');
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

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const session = await createSession(buildRequest());
      await runPipeline(session.session_id);
      navigate(`/sessions/${session.session_id}`);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Something went wrong — please try again'));
    } finally {
      setLoading(false);
    }
  }

  function buildRequest(): CreateFreeTextSessionRequest {
    return {
      free_text: freeText,
      guest_count: guestCount,
      meal_type: mealType,
      occasion,
      dietary_restrictions: restrictions,
      serving_time: servingTime || undefined,
    };
  }

  const canSubmit = !!freeText.trim();

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
