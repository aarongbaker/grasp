import { Link } from 'react-router-dom';
import { AuthoringSectionCard } from '../components/authoring/AuthoringSectionCard';
import { Button } from '../components/shared/Button';
import styles from './AuthoredRecipeWorkspacePage.module.css';

const sections = [
  {
    eyebrow: 'Foundation',
    title: 'Name the dish and the feeling you want on the pass',
    description:
      'Capture the dish in chef language first: what lands on the plate, what makes it memorable, and how polished it needs to feel before service.',
    prompt: '"Tonight this should eat like…"',
    aside:
      'Later slices can turn this into tighter draft fields. For now, this section is the visible contract that the workspace starts with culinary intent rather than technical structure.',
    bullets: ['Dish identity', 'Service style', 'What the guest should notice first'],
  },
  {
    eyebrow: 'Mise en place',
    title: 'Sketch the prep rhythm before you worry about detail',
    description:
      'Outline where the labor sits: what gets started early, what waits until pickup, and where the tricky handoffs will be once the draft is real.',
    prompt: '"The work opens with…, then it tightens at…"',
    aside:
      'This keeps timing language chef-readable now while creating a seam for later structured timing, station, and sequencing tools.',
    bullets: ['Early prep and holds', 'Last-minute finishing work', 'Any pinch points during pickup'],
  },
  {
    eyebrow: 'Advance work',
    title: 'Mark what can be made ahead without dulling the dish',
    description:
      'Use this area to think through holds, rests, pickups, and recovery notes so make-ahead guidance has a natural home in the draft.',
    prompt: '"Safe to hold if…, best refreshed by…"',
    aside:
      'Future authoring slices can attach ingredient, storage, and reheating detail here without changing the visible page rhythm.',
    bullets: ['Components that improve overnight', 'Elements that must stay last-minute', 'Recovery notes if service slips'],
  },
];

export function AuthoredRecipeWorkspacePage() {
  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.kicker}>Chef-authored workspace</p>
          <h1 className={styles.title}>Open a fresh page for a dish you already know how to talk through.</h1>
          <p className={styles.subtitle}>
            This draft room is for shaping one recipe in kitchen language: what the dish is, how the work unfolds,
            and what can be handled ahead. It stays separate from dinner planning on purpose.
          </p>
        </div>

        <aside className={styles.heroNote} aria-label="Draft status">
          <p className={styles.noteLabel}>Draft status</p>
          <p className={styles.noteValue}>Blank page, clear structure.</p>
          <p className={styles.noteText}>
            Later slices can add real authoring fields here. Right now the page should make the next questions feel obvious.
          </p>
        </aside>
      </header>

      <section className={styles.callout} aria-labelledby="workspace-approach-heading">
        <div>
          <p className={styles.calloutEyebrow}>Kitchen notebook</p>
          <h2 id="workspace-approach-heading" className={styles.calloutTitle}>
            Build the draft in passes, not all at once.
          </h2>
          <p className={styles.calloutText}>
            Start with the dish voice, then walk into prep, timing, and make-ahead judgment. Nothing here changes session
            state or pipeline behavior in this slice — the authored workspace stays a frontend shell with chef-first copy.
          </p>
        </div>

        <div className={styles.actions}>
          <Button size="lg">Continue this draft shell</Button>
          <Link to="/sessions/new" className={styles.secondaryLink}>
            Need to plan a full dinner instead?
          </Link>
        </div>
      </section>

      <section className={styles.grid} aria-label="Authoring sections">
        {sections.map((section) => (
          <AuthoringSectionCard
            key={section.title}
            eyebrow={section.eyebrow}
            title={section.title}
            description={section.description}
            prompt={section.prompt}
            aside={section.aside}
          >
            <ul className={styles.bulletList}>
              {section.bullets.map((bullet) => (
                <li key={bullet}>{bullet}</li>
              ))}
            </ul>
          </AuthoringSectionCard>
        ))}
      </section>

      <section className={styles.footerNote} aria-labelledby="separation-heading">
        <h2 id="separation-heading" className={styles.footerTitle}>
          Keep authored drafting and menu planning in their own lanes.
        </h2>
        <p className={styles.footerText}>
          Use <span className={styles.emphasis}>Plan a Dinner</span> when you are building a service around a menu idea.
          Use this workspace when you already have a dish in mind and want a chef-readable draft scaffold.
        </p>
      </section>
    </div>
  );
}
