import { apiFetch } from './client';
import type { UserProfile } from '../types/api';

export function getProfile(userId: string): Promise<UserProfile> {
  return apiFetch<UserProfile>(`/users/${userId}/profile`);
}
