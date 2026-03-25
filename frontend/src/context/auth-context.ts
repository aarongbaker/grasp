import { createContext } from 'react';
import type { UserProfile } from '../types/api';

export interface AuthState {
  token: string | null;
  userId: string | null;
  user: UserProfile | null;
  isAuthenticated: boolean;
  login: (token: string, refreshToken: string, userId: string) => void;
  logout: () => void;
  setUser: (user: UserProfile) => void;
}

export const AuthContext = createContext<AuthState | null>(null);
