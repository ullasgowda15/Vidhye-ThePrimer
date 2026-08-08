/**
 * The session: silent refresh, cookie-backed restore, and the third auth state
 * (§18.2, §14.5).
 *
 * Access tokens live ~15 minutes, so a parent reading the dashboard *will* hit a
 * 401 mid-session. The only acceptable outcome is that they never notice — and,
 * because refresh tokens rotate, that a burst of simultaneous 401s spends exactly
 * one refresh between them.
 *
 * The rest of this file guards the property the child experience depends on: the
 * refresh token is not something this code can hold, and one call at load restores
 * the session without the login screen ever appearing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError, errorMessage, NETWORK_ERROR } from '@/lib/api/client';
import { resetAuthBootstrapForTests, useAuthStore } from '@/store/auth';

function jsonResponse(status: number, body: unknown) {
  return { ok: status < 400, status, text: async () => JSON.stringify(body) };
}

beforeEach(() => {
  resetAuthBootstrapForTests();
  useAuthStore.setState({
    accessToken: null,
    status: 'anonymous',
    user: null,
    parentVerifiedUntil: null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('auth store', () => {
  it('has nowhere to put a refresh token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { access: 'a1' }));
    vi.stubGlobal('fetch', fetchMock);

    await useAuthStore.getState().login('priya@example.com', 'pw');

    expect(useAuthStore.getState().accessToken).toBe('a1');
    expect(useAuthStore.getState().status).toBe('authenticated');
    // The refresh half of the pair is an httpOnly cookie, so it is absent from
    // the store, absent from storage, and unreadable by any script on the page.
    expect('refreshToken' in useAuthStore.getState()).toBe(false);
    expect(localStorage.getItem('auth')).toBeNull();
    expect(JSON.stringify(localStorage)).not.toContain('refresh');
  });

  it('sends credentials on the auth endpoints so the cookie is stored', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { access: 'a1' }));
    vi.stubGlobal('fetch', fetchMock);

    await useAuthStore.getState().login('priya@example.com', 'pw');

    // Without `credentials: 'include'` the browser discards the Set-Cookie on a
    // cross-origin deploy and the session quietly stops surviving reloads.
    expect(fetchMock.mock.calls[0][1].credentials).toBe('include');
  });

  it('refreshes with no body at all — the cookie is the credential', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { access: 'a2' }));
    vi.stubGlobal('fetch', fetchMock);

    await useAuthStore.getState().refresh();

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/auth/refresh');
    expect(init.body).toBeUndefined();
    expect(useAuthStore.getState().accessToken).toBe('a2');
  });

  it('signs out locally even if ending the session server-side fails', async () => {
    useAuthStore.setState({ accessToken: 'a1', status: 'authenticated' });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    await useAuthStore.getState().logout();

    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().status).toBe('anonymous');
    expect(useAuthStore.getState().parentVerifiedUntil).toBeNull();
  });

  it('expires the parent gate', () => {
    useAuthStore.setState({ parentVerifiedUntil: Date.now() - 1 });
    expect(useAuthStore.getState().isParentVerified()).toBe(false);

    useAuthStore.getState().passParentGate();
    expect(useAuthStore.getState().isParentVerified()).toBe(true);
  });
});

describe('session restore', () => {
  it('restores the session from the cookie in one call', async () => {
    useAuthStore.setState({ status: 'loading' });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { access: 'restored' }));
    vi.stubGlobal('fetch', fetchMock);

    await useAuthStore.getState().bootstrap();

    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(useAuthStore.getState().accessToken).toBe('restored');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('bootstraps once however many times it is called', async () => {
    useAuthStore.setState({ status: 'loading' });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { access: 'restored' }));
    vi.stubGlobal('fetch', fetchMock);

    // StrictMode runs the bootstrap effect twice in development. A second refresh
    // would present a cookie the first had already rotated and blacklisted.
    await Promise.all([useAuthStore.getState().bootstrap(), useAuthStore.getState().bootstrap()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('settles on anonymous when there is no session, without ever claiming one', async () => {
    useAuthStore.setState({ status: 'loading' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'no cookie' })));

    await useAuthStore.getState().bootstrap();

    expect(useAuthStore.getState().status).toBe('anonymous');
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it('does not restore the parent gate', async () => {
    useAuthStore.setState({ status: 'loading', parentVerifiedUntil: null });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { access: 'restored' })));

    await useAuthStore.getState().bootstrap();

    // The child cold-starts straight into their story; an adult challenge still
    // stands between them and billing. That composition is the point (§14.2).
    expect(useAuthStore.getState().isParentVerified()).toBe(false);
  });
});

describe('silent refresh', () => {
  it('refreshes once and retries the original request', async () => {
    useAuthStore.setState({ accessToken: 'expired', status: 'authenticated' });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(200, { access: 'fresh' }))
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
    useAuthStore.setState({ accessToken: 'expired', status: 'authenticated' });

    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.endsWith('/auth/refresh')) {
        return Promise.resolve(jsonResponse(200, { access: 'fresh' }));
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
    // Rotation means a second refresh would present a blacklisted cookie and log
    // the parent out mid-read.
    expect(refreshCalls).toHaveLength(1);
  });

  it('drops the session when the refresh cookie is dead', async () => {
    useAuthStore.setState({ accessToken: 'expired', status: 'authenticated' });

    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(401, {}))
        .mockResolvedValueOnce(jsonResponse(401, { detail: 'blacklisted' })),
    );

    await expect(api.listChildren()).rejects.toBeInstanceOf(ApiError);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().status).toBe('anonymous');
  });
});

/**
 * A page load must not be able to end a session, and a page load happens on every
 * reload — so anything short of a definitive answer has to leave the session alone.
 *
 * This is the half of the lockout that lived on this side. A refused refresh was
 * treated as "your session is over", so a parent whose reloads had exhausted a rate
 * limit was signed out of a valid session and sent to a login form that was, by
 * then, also refusing them.
 */
describe('a refresh that fails without answering', () => {
  it('keeps a live session when the server errors', async () => {
    useAuthStore.setState({ accessToken: 'live', status: 'authenticated' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(500, { detail: 'boom' })));

    expect(await useAuthStore.getState().refresh()).toBeNull();

    // We learned nothing about the session, so nothing about it changes.
    expect(useAuthStore.getState().accessToken).toBe('live');
    expect(useAuthStore.getState().status).toBe('authenticated');
  });

  it('keeps a live session when the refresh itself is throttled', async () => {
    useAuthStore.setState({ accessToken: 'live', status: 'authenticated' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(429, { detail: 'throttled' })));

    await useAuthStore.getState().refresh();

    expect(useAuthStore.getState().status).toBe('authenticated');
  });

  it('keeps a live session when the device is offline', async () => {
    useAuthStore.setState({ accessToken: 'live', status: 'authenticated' });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await useAuthStore.getState().refresh();

    expect(useAuthStore.getState().accessToken).toBe('live');
  });

  it('still settles a cold start it cannot confirm', async () => {
    // Nothing to preserve here — there is no token either way — and holding
    // `loading` would leave ProtectedRoute on a blank screen for good.
    useAuthStore.setState({ accessToken: null, status: 'loading' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(503, {})));

    await useAuthStore.getState().bootstrap();

    expect(useAuthStore.getState().status).toBe('anonymous');
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

  it("never renders a server's HTML at a parent", () => {
    // Verbatim shape of what an unreachable database produced: Django's debug
    // page, 40KB of it, which this function used to hand straight to the login
    // form as the reason their password was wrong.
    const traceback =
      '<!DOCTYPE html>\n<html><title>OperationalError at /api/v1/auth/login</title>';
    const message = errorMessage(new ApiError('failed', 500, traceback));

    expect(message).not.toContain('<');
    expect(message).not.toContain('OperationalError');
    expect(message).toContain('not with your details');
  });

  it("does not blame the parent's details for a failure on our side", () => {
    // The caller's fallback describes the failure it *expected*. Using it for a
    // 500 is how a working password comes to look broken.
    const message = errorMessage(
      new ApiError('failed', 502, null),
      'That email and password did not match.',
    );

    expect(message).not.toContain('did not match');
  });

  it('reports an unreachable service as unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const caught = await api.login('priya@example.com', 'pw').catch((error: unknown) => error);

    // A dropped connection arrives as an ApiError like everything else, so no
    // caller has to special-case a second error shape to tell a parent the truth.
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(NETWORK_ERROR);
    expect(errorMessage(caught, 'That email and password did not match.')).toContain(
      'could not reach',
    );
  });

  it('turns a throttle into a wait a person can act on', () => {
    const error = new ApiError('failed', 429, {
      detail: 'Request was throttled. Expected available in 2428 seconds.',
    });

    // "2428 seconds" is true and unreadable.
    expect(errorMessage(error)).toBe(
      'That has been tried too many times just now. Please try again in 41 minutes.',
    );
  });

  it('still says something useful when a throttle gives no number', () => {
    expect(errorMessage(new ApiError('failed', 429, { detail: 'Throttled.' }))).toContain(
      'too many times',
    );
  });

  it('does not report a cancelled request as a failure', async () => {
    // React Query aborts in-flight queries constantly; an abort is the caller
    // getting what it asked for, and must not be dressed up as a network outage.
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new DOMException('The operation was aborted.', 'AbortError')),
    );

    const caught = await api.health(controller.signal).catch((error: unknown) => error);

    expect(caught).toBeInstanceOf(DOMException);
    expect(caught).not.toBeInstanceOf(ApiError);
  });
});
