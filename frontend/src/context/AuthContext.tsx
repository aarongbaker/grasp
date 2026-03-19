import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import type { UserProfile } from '../types/api';

interface AuthState {
  token: string | null;
  userId: string | null;
  user: UserProfile | null;
  isAuthenticated: boolean;
  login: (token: string, refreshToken: string, userId: string) => void;
  logout: () => void;
  setUser: (user: UserProfile) => void;
}

const AuthContext = createContext<AuthState | null>(null);

function decodeUserId(token: string): string | null {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.sub || null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('grasp_token'));
  const [userId, setUserId] = useState<string | null>(() => localStorage.getItem('grasp_user_id'));
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

  // Listen for auth expiry events from API client
  useEffect(() => {
    const handler = () => logout();
    window.addEventListener('grasp:auth-expired', handler);
    return () => window.removeEventListener('grasp:auth-expired', handler);
  }, [logout]);

  // Validate token on mount
  useEffect(() => {
    if (token && !userId) {
      const decoded = decodeUserId(token);
      if (decoded) {
        setUserId(decoded);
        localStorage.setItem('grasp_user_id', decoded);
      } else {
        logout();
      }
    }
  }, [token, userId, logout]);

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

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
