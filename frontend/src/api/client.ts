const configuredApiUrl = import.meta.env.VITE_API_URL?.trim();
const normalizedApiUrl = configuredApiUrl ? configuredApiUrl.replace(/\/$/, '') : '';
const API_BASE = normalizedApiUrl ? `${normalizedApiUrl}/api/v1` : '/api/v1';

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
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
  } catch {
    clearTimeout(timer);
    if (controller.signal.aborted) {
      throw new ApiError(0, 'Request timed out — is the server running?');
    }
    throw new ApiError(0, 'Network error — could not reach the server');
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
    throw new ApiError(res.status, body.detail || res.statusText);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}
