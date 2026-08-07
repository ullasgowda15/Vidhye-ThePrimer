/**
 * The silent-refresh interceptor and the auth store (§18.2).
 *
 * Access tokens live ~15 minutes, so a parent reading the dashboard *will* hit
 * a 401 mid-session. The only acceptable outcome is that they never notice —
 * and, because refresh tokens rotate, that a burst of simultaneous 401s spends
 * exactly one refresh between them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError, errorMessage } from '@/lib/api/client';
import { useAuthStore } from '@/store/auth';

function jsonResponse(status: number, body: unknown) {
  return { ok: status < 400, status, text: async () => JSON.stringify(body) };
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: null,
    refreshToken: null,
    user: null,
    parentVerifiedUntil: null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('auth store', () => {
  it('keeps tokens out of persistent storage', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { access: 'a1', refresh: 'r1' })),
    );

    await useAuthStore.getState().login('priya@example.com', 'pw');

    expect(useAuthStore.getState().accessToken).toBe('a1');
    // An XSS on a page showing a child's memories must not also yield a
    // fourteen-day refresh token.
    expect(localStorage.getItem('auth')).toBeNull();
    expect(JSON.stringify(localStorage)).not.toContain('r1');
  });

  it('replaces the refresh token on rotation', async () => {
    useAuthStore.setState({ accessToken: 'old', refreshToken: 'r1' });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { access: 'a2', refresh: 'r2' })),
    );

    await useAuthStore.getState().refresh();

    // Keeping r1 would mean presenting a blacklisted token on the next call.
    expect(useAuthStore.getState().refreshToken).toBe('r2');
    expect(useAuthStore.getState().accessToken).toBe('a2');
  });

  it('signs out locally even if blacklisting the token fails', async () => {
    useAuthStore.setState({ accessToken: 'a1', refreshToken: 'r1' });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    await useAuthStore.getState().logout();

    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().parentVerifiedUntil).toBeNull();
  });

  it('expires the parent gate', () => {
    useAuthStore.setState({ parentVerifiedUntil: Date.now() - 1 });
    expect(useAuthStore.getState().isParentVerified()).toBe(false);

    useAuthStore.getState().passParentGate();
    expect(useAuthStore.getState().isParentVerified()).toBe(true);
  });
});

describe('silent refresh', () => {
  it('refreshes once and retries the original request', async () => {
    useAuthStore.setState({ accessToken: 'expired', refreshToken: 'r1' });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(200, { access: 'fresh', refresh: 'r2' }))
      .mockResolvedValueOnce(jsonResponse(200, [{ id: 'c1', first_name: 'Aarav' }]));
    vi.stubGlobal('fetch', fetchMock);

    const children = await api.listChildren();

    expect(children).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // The retry carries the new token, not the stale one.
    const retryHeaders = fetchMock.mock.calls[2][1].headers as Record<string, string>;
    expect(retryHeaders.Authorization).toBe('Bearer fresh');
  });

  it('spends only one refresh when several requests expire together', async () => {
    useAuthStore.setState({ accessToken: 'expired', refreshToken: 'r1' });

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/auth/refresh')) {
        return Promise.resolve(jsonResponse(200, { access: 'fresh', refresh: 'r2' }));
      }
      const authorization = (fetchMock.mock.calls.at(-1)?.[1]?.headers ?? {}).Authorization;
      return Promise.resolve(
        authorization === 'Bearer fresh' ? jsonResponse(200, []) : jsonResponse(401, {}),
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    await Promise.all([api.listChildren(), api.listChildren(), api.listChildren()]);

    const refreshCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).endsWith('/auth/refresh'),
    );
    // Rotation means a second refresh would present a blacklisted token and
    // log the parent out mid-read.
    expect(refreshCalls).toHaveLength(1);
  });

  it('drops the session when the refresh token is dead', async () => {
    useAuthStore.setState({ accessToken: 'expired', refreshToken: 'r1' });

    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(401, {}))
        .mockResolvedValueOnce(jsonResponse(401, { detail: 'blacklisted' })),
    );

    await expect(api.listChildren()).rejects.toBeInstanceOf(ApiError);
    expect(useAuthStore.getState().accessToken).toBeNull();
  });
});

describe('error messages', () => {
  it('surfaces a DRF field error a parent can act on', () => {
    const error = new ApiError('failed', 400, { password: ['This password is too common.'] });
    expect(errorMessage(error)).toBe('This password is too common.');
  });

  it('surfaces the consent gate refusal', () => {
    const error = new ApiError('failed', 403, { detail: 'Parental consent is required.' });
    expect(errorMessage(error)).toBe('Parental consent is required.');
  });
});
