import { apiFetch } from './client';
import type {
  BillingRecoverySessionResponse,
  BillingRecoveryStatusResponse,
  BillingSessionResponse,
  BillingSetupSessionResponse,
  BillingSetupStatusResponse,
} from '../types/api';

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

export function getGenerationPaymentMethodStatus(): Promise<BillingSetupStatusResponse> {
  return apiFetch<BillingSetupStatusResponse>('/billing/generation/payment-method');
}

export function createGenerationSetupSession(sessionId?: string): Promise<BillingSetupSessionResponse> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  return apiFetch<BillingSetupSessionResponse>(`/billing/generation/setup${query}`, {
    method: 'POST',
  });
}

export function confirmGenerationPaymentMethod(): Promise<BillingSetupStatusResponse> {
  return apiFetch<BillingSetupStatusResponse>('/billing/generation/payment-method/confirm', {
    method: 'POST',
  });
}

export function getGenerationRecoveryStatus(sessionId: string): Promise<BillingRecoveryStatusResponse> {
  return apiFetch<BillingRecoveryStatusResponse>(`/billing/generation/recovery/${sessionId}`);
}

export function createGenerationRecoverySession(sessionId: string): Promise<BillingRecoverySessionResponse> {
  return apiFetch<BillingRecoverySessionResponse>('/billing/generation/recovery', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId }),
  });
}
