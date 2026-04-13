import { motion } from 'framer-motion';
import { ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import styles from './Hero.module.css';

export function Hero() {
  return (
    <section className={styles.section}>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, ease: 'easeOut' }}
        className={styles.inner}
      >
        <span className={styles.label}>
          Generative Scheduling &amp; Planning
        </span>

        <h1 className={styles.heading}>
          Your dinner service,
          <br />
          <span className={styles.headingAccent}>orchestrated.</span>
        </h1>

        <p className={styles.description}>
          GRASP turns a meal idea into a generated menu and a time-coordinated
          cooking schedule. Multi-course service, timed to land together.
        </p>

        <div className={styles.ctas}>
          <Link to="/register" className={styles.primaryBtn}>
            Get started
            <ArrowRight size={16} />
          </Link>
          <Link to="/login" className={styles.secondaryLink}>
            Sign in
          </Link>
        </div>
      </motion.div>

      {/* Mini Timeline Visual */}
      <motion.div
        initial={{ opacity: 0, y: 40 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 1, delay: 0.3, ease: 'easeOut' }}
        className={styles.timelineCard}
      >
        <div className={styles.timelineRows}>
          <div className={`${styles.gridLine} ${styles.gridLine1}`} />
          <div className={`${styles.gridLine} ${styles.gridLine2}`} />

          <TimelineRow
            label="Herb-Crusted Lamb"
            colorClass={styles.barTerracotta}
            start="10%"
            width="50%"
            delay={0.5}
          />
          <TimelineRow
            label="Roasted Root Vegetables"
            colorClass={styles.barSage}
            start="30%"
            width="40%"
            delay={0.7}
          />
          <TimelineRow
            label="Béarnaise Sauce"
            colorClass={styles.barAmber}
            start="65%"
            width="20%"
            delay={0.9}
          />
          <TimelineRow
            label="Chocolate Soufflé"
            colorClass={styles.barMaroon}
            start="40%"
            width="45%"
            delay={1.1}
          />
        </div>
      </motion.div>
    </section>
  );
}

function TimelineRow({
  label,
  colorClass,
  start,
  width,
  delay,
}: {
  label: string;
  colorClass: string;
  start: string;
  width: string;
  delay: number;
}) {
  return (
    <div className={styles.row}>
      <div className={styles.rowLabel}>{label}</div>
      <div className={styles.rowTrack}>
        <motion.div
          initial={{ width: 0, opacity: 0 }}
          animate={{ width, opacity: 1 }}
          transition={{ duration: 1.2, delay, ease: 'easeOut' }}
          className={`${styles.rowBar} ${colorClass}`}
          style={{ left: start }}
        />
      </div>
    </div>
  );
}
