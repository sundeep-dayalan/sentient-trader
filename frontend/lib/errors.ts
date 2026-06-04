import { ApiError } from '@/lib/api';

export type AppErrorTone = 'warning' | 'danger' | 'info';

export interface AppErrorCopy {
  title: string;
  message: string;
  tone: AppErrorTone;
  detail?: string;
  status?: number;
}

type ErrorContext = 'alpaca-summary' | 'alpaca-orders' | 'portfolio' | 'cancel' | 'generic';

function rawErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object' && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string') return message;
  }
  return '';
}

function statusFromMessage(message: string): number | undefined {
  const match = message.match(/\b(?:HTTP|status)\s+(\d{3})\b/i);
  if (!match) return undefined;
  const status = Number(match[1]);
  return Number.isFinite(status) ? status : undefined;
}

function statusFromError(error: unknown, message: string): number | undefined {
  if (error instanceof ApiError) return error.status;
  return statusFromMessage(message);
}

function errorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const payload = error.payload;
  if (!payload || typeof payload !== 'object' || !('error' in payload)) return null;
  const body = payload.error;
  if (!body || typeof body !== 'object' || !('code' in body)) return null;
  return typeof body.code === 'string' ? body.code : null;
}

function contextCopy(context: ErrorContext) {
  switch (context) {
    case 'alpaca-summary':
      return {
        title: 'Live brokerage data is unavailable',
        message: 'The account snapshot could not refresh. The dashboard will retry automatically.',
      };
    case 'alpaca-orders':
      return {
        title: 'Orders are temporarily unavailable',
        message: 'Alpaca order data could not refresh. Existing rows may be stale until the next retry.',
      };
    case 'portfolio':
      return {
        title: 'Portfolio history is temporarily unavailable',
        message: 'The chart could not refresh from Alpaca. It will retry automatically.',
      };
    case 'cancel':
      return {
        title: 'Cancel request failed',
        message: 'Alpaca did not accept the cancel request. Refresh orders and try again.',
      };
    default:
      return {
        title: 'Data is temporarily unavailable',
        message: 'This view could not refresh. It will retry automatically.',
      };
  }
}

export function formatAlpacaError(error: unknown, context: ErrorContext = 'generic'): AppErrorCopy {
  const raw = rawErrorMessage(error);
  const lower = raw.toLowerCase();
  const status = statusFromError(error, raw);
  const detail = status ? `HTTP ${status}` : undefined;
  const code = errorCode(error);

  if (code === 'RATE_LIMITED') {
    return {
      title: 'Request rate limit reached',
      message: 'The app is protecting the API from too many requests. Please wait briefly and retry.',
      tone: 'warning',
      detail,
      status,
    };
  }

  if (status === 429 || lower.includes('rate limit') || lower.includes('too many requests')) {
    return {
      title: 'Alpaca is rate limiting requests',
      message: 'Live brokerage data is paused briefly. The app will retry automatically.',
      tone: 'warning',
      detail,
      status,
    };
  }

  if (lower.includes('missing alpaca api credentials')) {
    return {
      title: 'Alpaca credentials are not configured',
      message: 'Add paper trading credentials to the backend environment before loading live data.',
      tone: 'danger',
      status,
    };
  }

  if (status === 401 || status === 403) {
    return {
      title: 'Sign in required',
      message: 'Your session does not have permission to load this data.',
      tone: 'warning',
      detail,
      status,
    };
  }

  if (status && status >= 500) {
    return {
      title: 'Brokerage data provider is unavailable',
      message: 'Alpaca or the backend returned a server error. The app will retry automatically.',
      tone: 'warning',
      detail,
      status,
    };
  }

  if (
    lower.includes('failed to fetch') ||
    lower.includes('networkerror') ||
    lower.includes('could not reach')
  ) {
    return {
      title: 'Backend connection interrupted',
      message: 'The app could not reach the API. Check the backend service and try again.',
      tone: 'danger',
      status,
    };
  }

  return {
    ...contextCopy(context),
    tone: 'warning',
    detail,
    status,
  };
}
