import { type FormEvent, type KeyboardEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { createSession, runPipeline } from '../api/sessions';
import { Button } from '../components/shared/Button';
import { Input, Textarea } from '../components/shared/Input';
import { Select } from '../components/shared/Select';
import { MEAL_TYPE_LABELS, OCCASION_LABELS, type MealType, type Occasion } from '../types/api';
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
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

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
      const session = await createSession({
        free_text: freeText,
        guest_count: guestCount,
        meal_type: mealType,
        occasion,
        dietary_restrictions: restrictions,
      });
      await runPipeline(session.session_id);
      navigate(`/sessions/${session.session_id}`);
    } catch (err: any) {
      setError(err.detail || err.message || 'Something went wrong — please try again');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.page}>
      <h1 className={styles.title}>Plan a Dinner</h1>
      <p className={styles.subtitle}>Describe the meal you're imagining and we'll handle the rest.</p>

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
    </div>
  );
}
