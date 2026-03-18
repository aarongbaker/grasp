import { Link } from 'react-router-dom';
import styles from './LandingNav.module.css';

export function LandingNav() {
  return (
    <nav className={styles.nav}>
      <Link to="/welcome" className={styles.logo}>
        GRASP
      </Link>
      <div className={styles.actions}>
        <Link to="/login" className={styles.signInLink}>
          Sign in
        </Link>
        <Link to="/register" className={styles.getStartedBtn}>
          Get started
        </Link>
      </div>
    </nav>
  );
}
