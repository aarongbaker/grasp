import { Link } from 'react-router-dom';
import { Button } from '../components/shared/Button';
import styles from './AuthoredRecipeWorkspacePage.module.css';

export function AuthoredRecipeWorkspacePage() {
  return (
    <div className={styles.page}>
      <div className={styles.hero}>
        <p className={styles.kicker}>Chef-authored workspace</p>
        <h1 className={styles.title}>Start a Recipe Draft</h1>
        <p className={styles.subtitle}>
          Shape a dish in kitchen language first — notes, flow, and service intent — then build it out in later passes.
        </p>
      </div>

      <section className={styles.card} aria-labelledby="draft-rhythm-heading">
        <div>
          <h2 id="draft-rhythm-heading" className={styles.cardTitle}>
            Draft in the rhythm you cook
          </h2>
          <p className={styles.cardText}>
            Begin with the dish in your own words, then refine timing, prep cadence, and make-ahead thinking as the draft takes shape.
          </p>
        </div>
        <div className={styles.actions}>
          <Button>Open drafting workspace</Button>
          <Link to="/" className={styles.secondaryLink}>
            Back to dashboard
          </Link>
        </div>
      </section>

      <section className={styles.note} aria-label="What stays separate">
        <p>
          Planning a full dinner service still lives in <span className={styles.emphasis}>Plan a Dinner</span>. This workspace is reserved for chef-authored recipes.
        </p>
      </section>
    </div>
  );
}
