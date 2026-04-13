import styles from './Footer.module.css';

export function Footer() {
  return (
    <footer className={styles.footer}>
      <div className={styles.container}>
        <div className={styles.brand}>
          <h2 className={styles.brandTitle}>GRASP</h2>
          <p className={styles.brandSub}>
            Generative Scheduling &amp; Planning
          </p>
        </div>

        <div className={styles.links}>
          <a href="#">GitHub</a>
          <a href="#">Documentation</a>
          <a href="#">About</a>
        </div>
      </div>

      <div className={styles.copyright}>
        &copy; {new Date().getFullYear()} GRASP Project. All rights reserved.
      </div>
    </footer>
  );
}
