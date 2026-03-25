import { useCallback, useEffect, useState, type ReactNode } from 'react';
import type { UserProfile } from '../types/api';
import { AuthContext } from './auth-context';

function decodeUserId(token: string): string | null {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.sub || null;
  } catch {
    return null;
  }
}

function getInitialToken(): string | null {
  return localStorage.getItem('grasp_token');
}

function getInitialUserId(token: string | null): string | null {
  const storedUserId = localStorage.getItem('grasp_user_id');
  if (storedUserId) return storedUserId;

  if (!token) return null;

  const decoded = decodeUserId(token);
  if (decoded) {
    localStorage.setItem('grasp_user_id', decoded);
    return decoded;
  }

  localStorage.removeItem('grasp_token');
  localStorage.removeItem('grasp_refresh_token');
  return null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getInitialToken());
  const [userId, setUserId] = useState<string | null>(() => getInitialUserId(getInitialToken()));
  const [user, setUser] = useState<UserProfile | null>(null);

  const login = useCallback((newToken: string, refreshToken: string, newUserId: string) => {
    localStorage.setItem('grasp_token', newToken);
    localStorage.setItem('grasp_refresh_token', refreshToken);
    localStorage.setItem('grasp_user_id', newUserId);
    setToken(newToken);
    setUserId(newUserId);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('grasp_token');
    localStorage.removeItem('grasp_refresh_token');
    localStorage.removeItem('grasp_user_id');
    setToken(null);
    setUserId(null);
    setUser(null);
  }, []);

  useEffect(() => {
    const handler = () => logout();
    window.addEventListener('grasp:auth-expired', handler);
    return () => window.removeEventListener('grasp:auth-expired', handler);
  }, [logout]);

  return (
    <AuthContext.Provider
      value={{
        token,
        userId,
        user,
        isAuthenticated: !!token && !!userId,
        login,
        logout,
        setUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
