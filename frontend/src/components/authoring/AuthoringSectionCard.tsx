import type { ReactNode } from 'react';
import styles from './AuthoringSectionCard.module.css';

interface AuthoringSectionCardProps {
  eyebrow: string;
  title: string;
  description: string;
  prompt: string;
  aside?: string;
  children?: ReactNode;
  validationMessages?: string[];
}

export function AuthoringSectionCard({
  eyebrow,
  title,
  description,
  prompt,
  aside,
  children,
  validationMessages = [],
}: AuthoringSectionCardProps) {
  const uniqueMessages = Array.from(new Set(validationMessages));

  return (
    <article className={`${styles.card} ${uniqueMessages.length > 0 ? styles.cardWarning : ''}`}>
      <div className={styles.header}>
        <p className={styles.eyebrow}>{eyebrow}</p>
        <h2 className={styles.title}>{title}</h2>
        <p className={styles.description}>{description}</p>
      </div>

      {uniqueMessages.length > 0 ? (
        <div className={styles.validationBlock} aria-label={`${title} validation guidance`}>
          <p className={styles.validationLabel}>Needs another pass</p>
          <ul className={styles.validationList}>
            {uniqueMessages.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}

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
