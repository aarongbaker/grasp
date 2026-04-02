import { apiFetch } from './client';
import type {
  CreateSessionRequest,
  PlannerReferenceResolutionRequest,
  PlannerReferenceResolutionResponse,
  Session,
  SessionResults,
} from '../types/api';

export function listSessions(userId: string): Promise<Session[]> {
  return apiFetch<Session[]>(`/users/${userId}/sessions`);
}

export function resolvePlannerReference(
  body: PlannerReferenceResolutionRequest,
): Promise<PlannerReferenceResolutionResponse> {
  return apiFetch<PlannerReferenceResolutionResponse>('/sessions/planner/resolve', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function createSession(body: CreateSessionRequest): Promise<Session> {
  return apiFetch<Session>('/sessions', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function runPipeline(sessionId: string): Promise<{ session_id: string; status: string; message: string }> {
  return apiFetch(`/sessions/${sessionId}/run`, {
    method: 'POST',
  });
}

export function getSession(sessionId: string): Promise<Session> {
  return apiFetch<Session>(`/sessions/${sessionId}`);
}

export function getSessionResults(sessionId: string): Promise<SessionResults> {
  return apiFetch<SessionResults>(`/sessions/${sessionId}/results`);
}

export function cancelSession(sessionId: string): Promise<{ session_id: string; status: string }> {
  return apiFetch(`/sessions/${sessionId}/cancel`, { method: 'POST' });
}

export function deleteSession(sessionId: string): Promise<void> {
  return apiFetch(`/sessions/${sessionId}`, { method: 'DELETE' });
}
