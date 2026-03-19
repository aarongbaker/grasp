import { apiFetch } from './client';
import type { TokenResponse, CreateUserRequest, UserProfile } from '../types/api';

export function login(email: string, password: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>('/auth/token', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export function refreshTokens(refreshToken: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>('/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

export function register(body: CreateUserRequest): Promise<UserProfile> {
  return apiFetch<UserProfile>('/users', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
