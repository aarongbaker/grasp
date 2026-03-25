import { useCallback, useRef, useState, type DragEvent } from 'react';
import styles from './FileUpload.module.css';

interface FileUploadProps {
  accept?: string;
  maxSizeMB?: number;
  onFile: (file: File) => void;
  disabled?: boolean;
}

export function FileUpload({ accept = '.pdf', maxSizeMB = 100, onFile, disabled }: FileUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    (file: File) => {
      setError(null);
      if (accept && !file.name.toLowerCase().endsWith(accept.replace('*', ''))) {
        setError(`Only ${accept} files are accepted`);
        return;
      }
      if (file.size > maxSizeMB * 1024 * 1024) {
        setError(`File too large. Maximum size is ${maxSizeMB} MB.`);
        return;
      }
      setFileName(file.name);
      onFile(file);
    },
    [accept, maxSizeMB, onFile],
  );

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      if (disabled) return;
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [disabled, handleFile],
  );

  return (
    <div>
      <div
        className={`${styles.dropzone} ${isDragging ? styles.active : ''}`}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="Upload file"
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click(); }}
      >
        <div className={styles.icon}>&#x1F4C4;</div>
        {fileName ? (
          <span className={styles.fileName}>{fileName}</span>
        ) : (
          <>
            <p className={styles.label}>
              Drop your PDF here or <strong>browse</strong>
            </p>
            <p className={styles.hint}>Maximum file size: {maxSizeMB} MB</p>
          </>
        )}
      </div>
      {error && <p style={{ color: 'var(--cost-negative)', fontSize: 'var(--text-xs)', marginTop: 'var(--space-sm)' }}>{error}</p>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className={styles.hidden}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
        }}
      />
    </div>
  );
}
