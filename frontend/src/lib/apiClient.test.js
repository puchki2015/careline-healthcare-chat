import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiRequest } from './apiClient';
import { supabase } from './supabase';

vi.mock('./runtimeConfig', () => ({
  runtimeConfig: { apiBaseUrl: 'http://careline.test/api/v1' },
}));
vi.mock('./logger', () => ({ logError: vi.fn(), logEvent: vi.fn() }));
vi.mock('./supabase', () => ({
  supabase: {
    auth: {
      getSession: vi.fn(),
      refreshSession: vi.fn(),
      signOut: vi.fn(),
    },
  },
}));

function response(status, payload = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: new Headers(),
    json: vi.fn().mockResolvedValue(payload),
  };
}

describe('authenticated API recovery', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    supabase.auth.getSession.mockResolvedValue({
      data: { session: { access_token: 'expired-token' } },
      error: null,
    });
    supabase.auth.signOut.mockResolvedValue({ error: null });
  });

  it('refreshes once after a 401 and retries with the new access token', async () => {
    fetch.mockResolvedValueOnce(response(401)).mockResolvedValueOnce(response(200, { items: [] }));
    supabase.auth.refreshSession.mockResolvedValue({
      data: { session: { access_token: 'fresh-token' } },
      error: null,
    });

    await expect(apiRequest('/chat/sessions')).resolves.toEqual({ items: [] });

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(supabase.auth.refreshSession).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[1][1].headers.get('Authorization')).toBe('Bearer fresh-token');
  });

  it('signs out locally when an expired session cannot be refreshed', async () => {
    fetch.mockResolvedValueOnce(response(401));
    supabase.auth.refreshSession.mockResolvedValue({
      data: { session: null },
      error: new Error('refresh failed'),
    });

    await expect(apiRequest('/chat/sessions')).rejects.toMatchObject({
      status: 401,
      code: 'authentication_required',
    });
    expect(supabase.auth.signOut).toHaveBeenCalledWith({ scope: 'local' });
  });
});
