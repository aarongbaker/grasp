import styles from './Skeleton.module.css';

interface SkeletonProps {
  variant?: 'text' | 'heading' | 'card' | 'timeline';
  width?: string;
  height?: string;
  count?: number;
}

export function Skeleton({ variant = 'text', width, height, count = 1 }: SkeletonProps) {
  const elements = Array.from({ length: count }, (_, i) => (
    <div
      key={i}
      className={`${styles.skeleton} ${styles[variant]}`}
      style={{ width, height }}
    />
  ));

  return <>{elements}</>;
}
