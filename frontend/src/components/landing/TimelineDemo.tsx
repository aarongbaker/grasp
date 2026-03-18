import { motion } from 'framer-motion';
import styles from './TimelineDemo.module.css';

const times = [
  '2:00 PM',
  '2:30 PM',
  '3:00 PM',
  '3:30 PM',
  '4:00 PM',
  '4:30 PM',
  '5:00 PM',
  '5:30 PM',
];

export function TimelineDemo() {
  return (
    <section className={styles.section}>
      <div className={styles.container}>
        <div className={styles.header}>
          <h2 className={styles.title}>A Sunday roast, orchestrated</h2>
          <p className={styles.subtitle}>
            Here's what GRASP produces for a three-course Sunday dinner. Every
            step timed, every dish synchronized.
          </p>
        </div>

        {/* Interactive-looking Timeline */}
        <div className={styles.timelineCard}>
          <div className={styles.scrollArea}>
            <div className={styles.scrollInner}>
              {/* Time Axis */}
              <div className={styles.timeAxis}>
                {times.map((time) => (
                  <div key={time} className={styles.timeMark}>
                    <span>{time}</span>
                    <div className={styles.timeGridLine} />
                  </div>
                ))}
              </div>

              {/* Swimlanes */}
              <div className={styles.lanes}>
                {/* Lane 1: Lamb */}
                <div className={styles.lane}>
                  <div className={styles.laneLabel}>Rack of Lamb</div>
                  <div className={styles.laneTrack}>
                    <div
                      className={`${styles.segment} ${styles.segTerracotta}`}
                      style={{ left: '10%', width: '15%' }}
                    >
                      Prep &amp; Crust
                    </div>
                    <div
                      className={`${styles.segment} ${styles.segTerracotta60}`}
                      style={{ left: '30%', width: '35%' }}
                    >
                      Roast (Oven)
                    </div>
                    <div
                      className={`${styles.segment} ${styles.segTerracotta40}`}
                      style={{ left: '68%', width: '15%' }}
                    >
                      Rest
                    </div>
                  </div>
                </div>

                {/* Lane 2: Vegetables */}
                <div className={styles.lane}>
                  <div className={styles.laneLabel}>Root Vegetables</div>
                  <div className={styles.laneTrack}>
                    <div
                      className={`${styles.segment} ${styles.segSage}`}
                      style={{ left: '15%', width: '12%' }}
                    >
                      Chop &amp; Toss
                    </div>
                    <div
                      className={`${styles.segment} ${styles.segSage60}`}
                      style={{ left: '40%', width: '40%' }}
                    >
                      Roast (Oven)
                    </div>
                  </div>
                </div>

                {/* Lane 3: Béarnaise */}
                <div className={styles.lane}>
                  <div className={styles.laneLabel}>Béarnaise Sauce</div>
                  <div className={styles.laneTrack}>
                    <div
                      className={`${styles.segment} ${styles.segAmber}`}
                      style={{ left: '5%', width: '10%' }}
                    >
                      Prep Ingredients
                    </div>
                    <motion.div
                      animate={{
                        boxShadow: [
                          '0 0 0 0 rgba(217,119,6,0)',
                          '0 0 0 4px rgba(217,119,6,0.2)',
                          '0 0 0 0 rgba(217,119,6,0)',
                        ],
                      }}
                      transition={{ duration: 2, repeat: Infinity }}
                      className={styles.activeSegment}
                      style={{ left: '65%', width: '15%' }}
                    >
                      Whisk &amp; Emulsify
                    </motion.div>
                  </div>
                </div>

                {/* Lane 4: Soufflé */}
                <div className={styles.lane}>
                  <div className={styles.laneLabel}>Chocolate Soufflé</div>
                  <div className={styles.laneTrack}>
                    <div
                      className={`${styles.segment} ${styles.segMaroon}`}
                      style={{ left: '20%', width: '20%' }}
                    >
                      Base &amp; Chill
                    </div>
                    <div
                      className={`${styles.segment} ${styles.segMaroon60}`}
                      style={{ left: '80%', width: '20%' }}
                    >
                      Bake (Oven)
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Current Step Card */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className={styles.currentStep}
        >
          <div className={styles.currentStepAccent} />
          <div className={styles.currentStepHeader}>
            <span className={styles.currentStepBadge}>CURRENT STEP</span>
            <span className={styles.currentStepTime}>3:15 PM</span>
          </div>
          <h3 className={styles.currentStepTitle}>Start the béarnaise</h3>
          <p className={styles.currentStepDesc}>
            The lamb is resting and the vegetables have 20 minutes left. You
            have a free burner on the stovetop. Begin whisking the egg yolks and
            vinegar reduction over low heat.
          </p>
        </motion.div>
      </div>
    </section>
  );
}
