import { useId, type SelectHTMLAttributes } from 'react';
import styles from './Select.module.css';

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: { value: string; label: string }[];
  error?: string;
}

export function Select({ label, options, error, className, id, ...props }: SelectProps) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const errorId = error ? `${selectId}-error` : undefined;

  return (
    <div className={styles.field}>
      {label && <label className={styles.label} htmlFor={selectId}>{label}</label>}
      <select
        id={selectId}
        className={`${styles.select} ${error ? styles.selectError : ''} ${className || ''}`}
        aria-invalid={error ? 'true' : undefined}
        aria-describedby={errorId}
        {...props}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      {error && <span id={errorId} className={styles.error}>{error}</span>}
    </div>
  );
}
