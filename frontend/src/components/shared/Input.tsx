import type { InputHTMLAttributes, TextareaHTMLAttributes } from 'react';
import styles from './Input.module.css';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}

export function Input({ label, error, className, ...props }: InputProps) {
  return (
    <div className={styles.field}>
      {label && <label className={styles.label}>{label}</label>}
      <input className={`${styles.input} ${className || ''}`} {...props} />
      {error && <span className={styles.error}>{error}</span>}
    </div>
  );
}

export function Textarea({ label, error, className, ...props }: TextareaProps) {
  return (
    <div className={styles.field}>
      {label && <label className={styles.label}>{label}</label>}
      <textarea className={`${styles.input} ${styles.textarea} ${className || ''}`} {...props} />
      {error && <span className={styles.error}>{error}</span>}
    </div>
  );
}
