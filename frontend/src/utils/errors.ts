import { ApiError } from '../api/client';
import type { AuthoredRecipeValidationDetail } from '../types/api';

export function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.kind === 'startup-config') {
      return `The API responded with a startup/configuration failure: ${error.detail}`;
    }
    return error.detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function getAuthoredRecipeValidationDetail(error: unknown): AuthoredRecipeValidationDetail | null {
  if (!(error instanceof ApiError) || error.kind !== 'authored-validation') {
    return null;
  }

  const payload = error.payload;
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { detail?: unknown }).detail)) {
    return null;
  }

  return payload as AuthoredRecipeValidationDetail;
}
