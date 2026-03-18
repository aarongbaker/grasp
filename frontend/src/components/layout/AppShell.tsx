import { useEffect } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { getProfile } from '../../api/users';
import { useAuth } from '../../context/AuthContext';
import { Sidebar } from './Sidebar';
import styles from './AppShell.module.css';

export function AppShell() {
  const { isAuthenticated, userId, setUser } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isAuthenticated) {
      navigate('/welcome', { replace: true });
    }
  }, [isAuthenticated, navigate]);

  // Fetch user profile on mount
  useEffect(() => {
    if (userId) {
      getProfile(userId).then(setUser).catch(() => {});
    }
  }, [userId, setUser]);

  if (!isAuthenticated) return null;

  return (
    <div className={styles.layout}>
      <Sidebar />
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
