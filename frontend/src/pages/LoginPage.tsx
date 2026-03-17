import { type FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { login } from '../api/auth';
import { useAuth } from '../context/AuthContext';
import { Button } from '../components/shared/Button';
import { Input } from '../components/shared/Input';
import styles from './LoginPage.module.css';

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const auth = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await login(email, password);
      const payload = JSON.parse(atob(res.access_token.split('.')[1]));
      auth.login(res.access_token, payload.sub);
      navigate('/', { replace: true });
    } catch (err: any) {
      setError(err.detail || 'Failed to sign in');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <h1 className={styles.logo}>grasp</h1>
        <p className={styles.subtitle}>Sign in to plan your next dinner</p>
        <form className={styles.form} onSubmit={handleSubmit}>
          {error && <div className={styles.error}>{error}</div>}
          <Input
            type="email"
            placeholder="chef@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
          />
          <Input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <Button type="submit" fullWidth disabled={loading}>
            {loading ? 'Signing in...' : 'Sign in'}
          </Button>
        </form>
        <p className={styles.switchLink}>
          New here? <Link to="/register">Create an account</Link>
        </p>
      </div>
    </div>
  );
}
