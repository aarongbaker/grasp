import { motion } from 'framer-motion';
import {
  ClipboardPenLine,
  Sparkles,
  BookOpenText,
  GitBranch,
  Clock,
  ChefHat,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import styles from './Pipeline.module.css';

const stages: { title: string; description: string; icon: LucideIcon }[] = [
  {
    title: 'Describe',
    description: 'You describe the meal, guest count, occasion, and service time.',
    icon: ClipboardPenLine,
  },
  {
    title: 'Generate',
    description:
      'Claude proposes dishes and core recipe structure from your menu intent.',
    icon: Sparkles,
  },
  {
    title: 'Shape',
    description:
      'The planner turns menu intent into workable dishes, timing assumptions, and service-ready structure before scheduling begins.',
    icon: BookOpenText,
  },
  {
    title: 'Graph',
    description:
      'Dependency analysis identifies prep order and parallel opportunities.',
    icon: GitBranch,
  },
  {
    title: 'Schedule',
    description:
      'Constraint-aware scheduling merges every dish into one service timeline.',
    icon: Clock,
  },
  {
    title: 'Render',
    description:
      'You get step-by-step guidance with timing, equipment flow, and sequencing.',
    icon: ChefHat,
  },
];

const containerVariants = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: { staggerChildren: 0.15 },
  },
};

const itemVariants = {
  hidden: { opacity: 0, y: 30 },
  show: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, ease: 'easeOut' as const },
  },
};

export function Pipeline() {
  return (
    <section className={styles.section}>
      <div className={styles.container}>
        <div className={styles.header}>
          <h2 className={styles.title}>
            From menu intent to coordinated cooking
          </h2>
          <p className={styles.subtitle}>
            A six-stage AI pipeline that turns a dinner idea into a structured,
            synchronized schedule.
          </p>
        </div>

        <motion.div
          variants={containerVariants}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: '-100px' }}
          className={styles.grid}
        >
          <div className={styles.connectingLine} />

          {stages.map((stage, index) => (
            <motion.div
              key={stage.title}
              variants={itemVariants}
              className={styles.card}
            >
              <div className={styles.iconBox}>
                <stage.icon />
              </div>
              <div className={styles.cardHeader}>
                <span className={styles.cardNumber}>
                  0{index + 1}
                </span>
                <h3 className={styles.cardTitle}>{stage.title}</h3>
              </div>
              <p className={styles.cardDesc}>{stage.description}</p>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
