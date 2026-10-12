import { runtimeConfig } from './runtimeConfig';
import { logError, logEvent } from './logger';
import { supabase } from './supabase';

export class ApiError extends Error {
  constructor({ status, code, message, requestId }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

/** Convert Careline's stable error envelope into a useful UI-safe error. */
async function createApiError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // A proxy can return HTML or an empty body. Keep the fallback generic.
  }
  return new ApiError({
    status: response.status,
    code: payload?.error?.code ?? 'unexpected_api_error',
    message: payload?.error?.message ?? 'Careline could not complete the request.',
    requestId: payload?.error?.request_id ?? response.headers.get('x-request-id'),
  });
}

let refreshPromise = null;

async function refreshAccessToken() {
  // Share one refresh when several requests discover expiry together. This
  // avoids racing refresh-token rotation in Supabase.
  if (!refreshPromise) {
    refreshPromise = supabase.auth
      .refreshSession()
      .then(({ data, error }) => {
        if (error || !data.session?.access_token) throw error || new Error('No refreshed session');
        return data.session.access_token;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

async function authenticatedFetch(path, options, accessToken) {
  const headers = new Headers(options.headers);
  headers.set('Authorization', `Bearer ${accessToken}`);
  headers.set('Accept', 'application/json');
  if (options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  return fetch(`${runtimeConfig.apiBaseUrl}${path}`, { ...options, headers });
}

/**
 * Call FastAPI with the freshest Supabase access token.
 *
 * Callers pass normal fetch options. Identity is never accepted from the UI;
 * the backend derives it from this signed token.
 */
export async function apiRequest(path, options = {}) {
  if (!runtimeConfig || !supabase) {
    throw new ApiError({
      status: 0,
      code: 'frontend_not_configured',
      message: 'The Careline frontend is not configured.',
      requestId: null,
    });
  }

  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session?.access_token) {
    throw new ApiError({
      status: 401,
      code: 'authentication_required',
      message: 'Please sign in again to continue.',
      requestId: null,
    });
  }

  let response;
  try {
    response = await authenticatedFetch(path, options, data.session.access_token);
    if (response.status === 401) {
      try {
        const refreshedToken = await refreshAccessToken();
        logEvent('api_access_token_refreshed');
        response = await authenticatedFetch(path, options, refreshedToken);
      } catch (refreshError) {
        logError('api_access_token_refresh_failed', refreshError);
        await supabase.auth.signOut({ scope: 'local' }).catch(() => {});
        throw new ApiError({
          status: 401,
          code: 'authentication_required',
          message: 'Your session expired. Please sign in again.',
          requestId: null,
        });
      }
    }
  } catch (error_) {
    if (error_ instanceof ApiError) throw error_;
    logError('api_network_failed', error_);
    throw new ApiError({
      status: 0,
      code: 'network_unavailable',
      message: 'Careline cannot reach the server. Check your connection and try again.',
      requestId: null,
    });
  }

  if (!response.ok) {
    const apiError = await createApiError(response);
    logError('api_request_failed', apiError);
    throw apiError;
  }

  if (response.status === 204) return null;
  return response.json();
}

/**
 * Admin access is resolved through the protected backend instead of trusting
 * browser state. A 403 means the user is a patient; other failures are real
 * availability/authentication problems and must remain visible.
 */
export async function resolveLandingRole() {
  try {
    await apiRequest('/admin/chat/sessions?limit=1');
    return 'admin';
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) return 'patient';
    throw error;
  }
}
