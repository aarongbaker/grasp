import { apiFetch } from './client';
import type { KitchenConfig, Equipment, UserProfile, EquipmentCategory } from '../types/api';

export function getProfile(userId: string): Promise<UserProfile> {
  return apiFetch<UserProfile>(`/users/${userId}/profile`);
}

export function updateKitchen(
  userId: string,
  data: Partial<Pick<KitchenConfig, 'max_burners' | 'max_oven_racks' | 'has_second_oven' | 'max_second_oven_racks'>>,
): Promise<KitchenConfig> {
  return apiFetch<KitchenConfig>(`/users/${userId}/kitchen`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function updateDietaryDefaults(
  userId: string,
  dietary_defaults: string[],
): Promise<{ dietary_defaults: string[] }> {
  return apiFetch(`/users/${userId}/dietary-defaults`, {
    method: 'PUT',
    body: JSON.stringify({ dietary_defaults }),
  });
}

export function addEquipment(
  userId: string,
  data: { name: string; category: EquipmentCategory; unlocks_techniques?: string[] },
): Promise<Equipment> {
  return apiFetch<Equipment>(`/users/${userId}/equipment`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function deleteEquipment(userId: string, equipmentId: string): Promise<void> {
  return apiFetch(`/users/${userId}/equipment/${equipmentId}`, { method: 'DELETE' });
}
