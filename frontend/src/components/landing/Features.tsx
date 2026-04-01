import { motion } from 'framer-motion';
import styles from './Features.module.css';

const features = [
  {
    title: 'Menu-Intent Planning',
    description:
      'Start with the dinner you want to cook, not a rigid form. GRASP turns that intent into a workable menu and cooking plan.',
  },
  {
    title: 'Multi-Course Coordination',
    description:
      'Plan a 4-course dinner and get a single unified timeline. No more juggling timers and hoping the sauce finishes when the roast does.',
  },
  {
    title: 'Dependency Graphs',
    description:
      'GRASP understands that stock must simmer before the risotto starts, and that dessert can chill while mains cook.',
  },
  {
    title: 'Equipment-Aware Scheduling',
    description:
      "Maximize your kitchen's throughput. Two burners, one oven, and a sous vide? GRASP schedules around your actual equipment.",
  },
  {
    title: 'Curated Culinary Grounding',
    description:
      'Behind the scenes, curated culinary references can sharpen timing, technique, and service decisions without taking over the workflow.',
  },
  {
    title: 'Step-by-Step Timeline',
    description:
      'A clear, minute-by-minute guide. Each step tells you exactly what to do, when, and on which burner.',
  },
];

export function Features() {
  return (
    <section className={styles.section}>
      <div className={styles.container}>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className={styles.header}
        >
          <h2 className={styles.title}>Built for serious home cooks</h2>
        </motion.div>

        <div className={styles.grid}>
          {features.map((feature, index) => (
            <motion.div
              key={feature.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
              className={styles.card}
            >
              <h3 className={styles.cardTitle}>{feature.title}</h3>
              <p className={styles.cardDesc}>{feature.description}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
