import type { ReactNode } from 'react';
import styles from './AuthoringSectionCard.module.css';

interface AuthoringSectionCardProps {
  eyebrow: string;
  title: string;
  description: string;
  prompt: string;
  aside?: string;
  children?: ReactNode;
}

export function AuthoringSectionCard({
  eyebrow,
  title,
  description,
  prompt,
  aside,
  children,
}: AuthoringSectionCardProps) {
  return (
    <article className={styles.card}>
      <div className={styles.header}>
        <p className={styles.eyebrow}>{eyebrow}</p>
        <h2 className={styles.title}>{title}</h2>
        <p className={styles.description}>{description}</p>
      </div>

      <div className={styles.promptBlock}>
        <p className={styles.promptLabel}>Start with</p>
        <p className={styles.prompt}>{prompt}</p>
      </div>

      {children ? <div className={styles.body}>{children}</div> : null}

      {aside ? (
        <aside className={styles.aside} aria-label={`${title} guidance`}>
          {aside}
        </aside>
      ) : null}
    </article>
  );
}
