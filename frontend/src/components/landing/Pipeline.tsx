import { motion } from 'framer-motion';
import {
  BookOpen,
  Database,
  Sparkles,
  GitBranch,
  Clock,
  ChefHat,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import styles from './Pipeline.module.css';

const stages: { title: string; description: string; icon: LucideIcon }[] = [
  {
    title: 'Ingest',
    description: 'Your cookbook PDFs and recipe URLs are parsed and chunked.',
    icon: BookOpen,
  },
  {
    title: 'Embed',
    description:
      'OpenAI embeddings index your recipes into Pinecone vector store.',
    icon: Database,
  },
  {
    title: 'Generate',
    description:
      'Claude creates new recipes informed by your collection via RAG.',
    icon: Sparkles,
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
      'Constraint solver merges all dishes into an optimal timeline.',
    icon: Clock,
  },
  {
    title: 'Render',
    description:
      'Step-by-step instructions with precise timing for every burner.',
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
            From cookbooks to coordinated cooking
          </h2>
          <p className={styles.subtitle}>
            A six-stage AI pipeline that understands your recipes, builds
            dependency graphs, and merges everything into a parallel schedule.
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
