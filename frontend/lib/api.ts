import { createBrowserClient } from '@/lib/supabase-browser';

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function apiBaseUrl(): string {
  const url = import.meta.env.VITE_BACKEND_API_URL;
  if (!url) {
    throw new Error('VITE_BACKEND_API_URL is not configured');
  }
  return url.replace(/\/+$/, '');
}

function errorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = payload.detail;
    if (typeof detail === 'string') return detail;
    if (
      detail &&
      typeof detail === 'object' &&
      'errorMessage' in detail &&
      typeof detail.errorMessage === 'string'
    ) {
      return detail.errorMessage;
    }
    return JSON.stringify(detail);
  }
  if (
    payload &&
    typeof payload === 'object' &&
    'error' in payload &&
    typeof payload.error === 'string'
  ) {
    return payload.error;
  }
  return fallback;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const supabase = createBrowserClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers = new Headers(init.headers);
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json');
  }
  if (session?.access_token) {
    headers.set('Authorization', `Bearer ${session.access_token}`);
  }

  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers,
    cache: 'no-store',
  });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(
      errorMessage(payload, `API returned HTTP ${response.status}`),
      response.status,
      payload,
    );
  }

  return payload as T;
}
