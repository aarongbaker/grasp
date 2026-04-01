import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/useAuth';
import { PATHWAYS } from './pathways';
import styles from './Sidebar.module.css';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: '\u25A3' },
  ...PATHWAYS.map((pathway) => ({
    to: pathway.to,
    label: pathway.navLabel,
    icon: pathway.icon,
  })),
  { to: '/profile', label: 'Kitchen', icon: '\u2318' },
];

export function Sidebar() {
  const location = useLocation();
  const { user, logout } = useAuth();

  return (
    <aside className={styles.sidebar}>
      <div className={styles.logo}>
        <div className={styles.logoText}>GRASP</div>
        <div className={styles.logoSub}>chef planning workspace</div>
      </div>

      <nav className={styles.nav}>
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className={`${styles.navLink} ${
              location.pathname === item.to ? styles.navLinkActive : ''
            }`}
          >
            <span className={styles.navIcon}>{item.icon}</span>
            {item.label}
          </Link>
        ))}
      </nav>

      <div className={styles.footer}>
        <div className={styles.userInfo}>
          <div>
            <div className={styles.userName}>{user?.name || 'Chef'}</div>
            <div className={styles.userEmail}>{user?.email || ''}</div>
          </div>
          <button onClick={logout} className={styles.logoutBtn} aria-label="Sign out">
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
