import type { AuthoredRecipeValidationDetail } from '../types/api';

const configuredApiUrl = import.meta.env.VITE_API_URL?.trim();
const normalizedApiUrl = configuredApiUrl ? configuredApiUrl.replace(/\/$/, '') : '';
const API_BASE = normalizedApiUrl ? `${normalizedApiUrl}/api/v1` : '/api/v1';

export type ApiErrorKind =
  | 'http'
  | 'timeout'
  | 'network-unreachable'
  | 'network-offline'
  | 'startup-config'
  | 'authored-validation';

export class ApiError extends Error {
  status: number;
  detail: string;
  kind: ApiErrorKind;
  payload?: unknown;

  constructor(status: number, detail: string, kind: ApiErrorKind = 'http', payload?: unknown) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.kind = kind;
    this.payload = payload;
  }
}

export function isAuthoredRecipeValidationDetail(value: unknown): value is AuthoredRecipeValidationDetail {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const detail = (value as { detail?: unknown }).detail;
  if (!Array.isArray(detail) || detail.length === 0) {
    return false;
  }

  return detail.every((issue) => {
    if (!issue || typeof issue !== 'object') {
      return false;
    }

    const candidate = issue as { type?: unknown; loc?: unknown; msg?: unknown };
    return (
      typeof candidate.type === 'string' &&
      Array.isArray(candidate.loc) &&
      candidate.loc.every((segment) => typeof segment === 'string' || typeof segment === 'number') &&
      typeof candidate.msg === 'string'
    );
  });
}

function looksLikeStartupConfigFailure(detail: string): boolean {
  const normalized = detail.toLowerCase();
  return [
    'must be set',
    'check langgraph_checkpoint_url',
    'check postgres connectivity',
    'check postgres permissions',
    'cors_allowed_origins',
    'jwt_secret_key',
    'production domain',
  ].some((snippet) => normalized.includes(snippet));
}

function classifyTransportError(_error: unknown, controller: AbortController): ApiError {
  if (controller.signal.aborted) {
    return new ApiError(
      0,
      'Request timed out while the server was processing your upload. The API may be slow, but it is still reachable.',
      'timeout',
    );
  }

  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    return new ApiError(
      0,
      'You appear to be offline, so the upload could not reach the API.',
      'network-offline',
    );
  }

  return new ApiError(
    0,
    'Could not reach the API. The backend may be down, still starting, or blocked by a local network issue.',
    'network-unreachable',
  );
}

/** Tracks whether a token refresh is already in flight so concurrent
 *  401s don't trigger multiple refresh calls. */
let refreshPromise: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  const refreshToken = localStorage.getItem('grasp_refresh_token');
  if (!refreshToken) return false;

  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) return false;

    const data = await res.json();
    localStorage.setItem('grasp_token', data.access_token);
    localStorage.setItem('grasp_refresh_token', data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

async function attemptRefresh(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = tryRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function rawFetch(
  path: string,
  options: RequestInit & { timeout?: number } = {},
): Promise<{ res: Response; status: number }> {
  const token = localStorage.getItem('grasp_token');

  const headers: Record<string, string> = {
    ...((options.headers as Record<string, string>) || {}),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const { timeout = 30_000, ...fetchOptions } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    clearTimeout(timer);
    throw classifyTransportError(error, controller);
  }
  clearTimeout(timer);

  return { res, status: res.status };
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { timeout?: number } = {},
): Promise<T> {
  let { res, status } = await rawFetch(path, options);

  if (status === 401) {
    const refreshed = await attemptRefresh();
    if (refreshed) {
      ({ res, status } = await rawFetch(path, options));
    }
  }

  if (status === 401) {
    localStorage.removeItem('grasp_token');
    localStorage.removeItem('grasp_refresh_token');
    localStorage.removeItem('grasp_user_id');
    window.dispatchEvent(new CustomEvent('grasp:auth-expired'));
    throw new ApiError(401, 'Session expired');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));

    if (status === 422 && path === '/authored-recipes' && isAuthoredRecipeValidationDetail(body)) {
      throw new ApiError(status, 'The recipe draft needs more detail before it can be saved.', 'authored-validation', body);
    }

    const detail = typeof body.detail === 'string' ? body.detail : res.statusText;
    const kind = looksLikeStartupConfigFailure(detail) ? 'startup-config' : 'http';
    throw new ApiError(status, detail, kind, body);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}
