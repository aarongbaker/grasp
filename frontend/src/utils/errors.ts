import { ApiError } from '../api/client';

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
