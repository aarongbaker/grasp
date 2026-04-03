import { type FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { register, login } from '../api/auth';
import { useAuth } from '../context/useAuth';
import { Button } from '../components/shared/Button';
import { Input } from '../components/shared/Input';
import { getErrorMessage } from '../utils/errors';
import styles from './LoginPage.module.css'; // Reuse login styles

export function RegisterPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [maxBurners, setMaxBurners] = useState(4);
  const [maxOvenRacks, setMaxOvenRacks] = useState(2);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const auth = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');

    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setLoading(true);
    try {
      await register({
        name,
        email,
        password,
        max_burners: maxBurners,
        max_oven_racks: maxOvenRacks,
        ...(inviteCode.trim() && { invite_code: inviteCode.trim() }),
      });
      const res = await login(email, password);
      const payload = JSON.parse(atob(res.access_token.split('.')[1]));
      auth.login(res.access_token, res.refresh_token, payload.sub);
      navigate('/', { replace: true });
    } catch (err: unknown) {
      const detail = getErrorMessage(err, 'Failed to create account');
      if (detail.includes('already exists')) {
        setError('An account with this email already exists. Try signing in instead.');
      } else {
        setError(detail);
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.page}>
      <Link to="/welcome" className={styles.backLink}>← Back</Link>
      <div className={styles.card}>
        <h1 className={styles.logo}>GRASP</h1>
        <p className={styles.subtitle}>Set up your kitchen</p>
        <form className={styles.form} onSubmit={handleSubmit}>
          {error && <div className={styles.error}>{error}</div>}
          <Input
            label="Name"
            placeholder="Chef's name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
          />
          <Input
            label="Email"
            type="email"
            placeholder="chef@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <Input
            label="Password"
            type="password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
          />
          <Input
            label="Invite code"
            placeholder="Invite code"
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
          />
          <Input
            label="Stovetop burners"
            type="number"
            min={1}
            max={10}
            value={maxBurners}
            onChange={(e) => setMaxBurners(Number(e.target.value))}
          />
          <Input
            label="Oven racks"
            type="number"
            min={1}
            max={6}
            value={maxOvenRacks}
            onChange={(e) => setMaxOvenRacks(Number(e.target.value))}
          />
          <Button type="submit" fullWidth disabled={loading}>
            {loading ? 'Creating account...' : 'Create account'}
          </Button>
        </form>
        <p className={styles.switchLink}>
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
