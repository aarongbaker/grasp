import { motion } from 'framer-motion';
import styles from './TechStack.module.css';

const stack = [
  { name: 'Claude', role: 'Recipe generation & reasoning', brand: 'Anthropic' },
  { name: 'OpenAI', role: 'Text embeddings for semantic search', brand: 'Embeddings' },
  { name: 'Pinecone', role: 'Vector database for cookbook RAG', brand: 'Vector DB' },
  { name: 'LangGraph', role: 'State machine orchestration', brand: 'Orchestration' },
];

export function TechStack() {
  return (
    <section className={styles.section}>
      <div className={styles.container}>
        <div className={styles.header}>
          <h2 className={styles.title}>The stack behind the schedule</h2>
          <p className={styles.subtitle}>
            GRASP orchestrates multiple AI systems through a LangGraph state
            machine.
          </p>
        </div>

        <div className={styles.grid}>
          {stack.map((tech, index) => (
            <motion.div
              key={tech.name}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: index * 0.1 }}
              className={styles.card}
            >
              <div className={styles.cardBrand}>{tech.brand}</div>
              <h3 className={styles.cardName}>{tech.name}</h3>
              <p className={styles.cardRole}>{tech.role}</p>
            </motion.div>
          ))}
        </div>

        {/* Architectural Diagram */}
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, delay: 0.4 }}
          className={styles.diagram}
        >
          <div className={styles.diagramNode}>User Request</div>
          <div className={styles.diagramArrow} />
          <div className={styles.diagramCenter}>
            LangGraph
            <div className={styles.diagramCenterSub}>Claude + Pinecone</div>
          </div>
          <div className={styles.diagramArrow} />
          <div className={styles.diagramOutput}>Schedule Output</div>
        </motion.div>
      </div>
    </section>
  );
}
