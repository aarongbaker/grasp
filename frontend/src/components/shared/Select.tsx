import type { SelectHTMLAttributes } from 'react';
import styles from './Select.module.css';

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: { value: string; label: string }[];
}

export function Select({ label, options, className, ...props }: SelectProps) {
  return (
    <div className={styles.field}>
      {label && <label className={styles.label}>{label}</label>}
      <select className={`${styles.select} ${className || ''}`} {...props}>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}
