import { apiFetch } from './client';
import type { BillingSessionResponse } from '../types/api';

export function createCheckoutSession(): Promise<BillingSessionResponse> {
  return apiFetch<BillingSessionResponse>('/billing/checkout', {
    method: 'POST',
  });
}

export function createPortalSession(): Promise<BillingSessionResponse> {
  return apiFetch<BillingSessionResponse>('/billing/portal', {
    method: 'POST',
  });
}
