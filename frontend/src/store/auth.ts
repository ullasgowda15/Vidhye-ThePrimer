/**
 * Parent session state (PRODUCT.md §18.2, §14.2, §14.5).
 *
 * **The access token lives in memory; the refresh token lives nowhere the client
 * can see it.** The backend sets it as an `httpOnly`, `SameSite=Lax` cookie scoped
 * to `/api/v1/auth`, so this file has no `refreshToken` field at all — an XSS on a
 * page showing a child's memories cannot read it, and neither can we.
 *
 * That is what makes a cold start survivable. The child experience runs under a
 * parent session and a three-year-old cannot log in, so a morning, a reboot or an
 * evicted tab must not drop them at a login form (§14.5). On load the app makes
 * **exactly one** refresh call: that call *is* session restore.
 *
 * `status` is the third state that makes this work. Without it, `ProtectedRoute`
 * would see `accessToken === null` during the bootstrap refresh and flash the
 * login screen on every single load before redirecting back.
 *
 * The **parent gate** (§14.2) also lives here and is deliberately *not* restored
 * by the cookie: it is a short-lived "an adult is holding the tablet" flag, not a
 * claim about identity. The child cold-starts straight into their story; an adult
 * challenge still stands between them and billing. That composition is the point.
 */

import { create } from 'zustand';

import { ApiError, api, connectAuth, type Me, type User } from '@/lib/api/client';

/** How long one adult challenge keeps the parent area open. */
export const PARENT_GATE_TTL_MS = 15 * 60 * 1000;

/**
 * How long to wait before retrying a refresh that came back 401.
 *
 * Long enough for another tab's rotated `Set-Cookie` to reach the shared jar,
 * short enough that a session which really has ended still resolves promptly —
 * this only ever delays reaching a login form. See `refresh`.
 */
export const REFRESH_RACE_RETRY_MS = 250;

/**
 * Whether we know yet.
 *
 * `loading` is the state the app starts in and holds until the bootstrap refresh
 * resolves. Treating it as `anonymous` is the bug this type exists to prevent.
 */
export type AuthStatus = 'loading' | 'anonymous' | 'authenticated';

interface AuthState {
  accessToken: string | null;
  status: AuthStatus;
  user: User | Me | null;
  /** Epoch ms until which the adult challenge counts as passed. */
  parentVerifiedUntil: number | null;

  isAuthenticated: () => boolean;
  isParentVerified: () => boolean;

  signup: (payload: { email: string; password: string; display_name?: string }) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Spend the refresh cookie for a new access token, or null if the session is over. */
  refresh: () => Promise<string | null>;
  /** One refresh at app load. Idempotent — calling it again returns the same result. */
  bootstrap: () => Promise<void>;
  setUser: (user: Me) => void;
  passParentGate: () => void;
  clearParentGate: () => void;
}

/**
 * In-flight refresh, shared by every caller.
 *
 * A dashboard fires several queries at once; when the access token expires they
 * all 401 together. Without this, each would spend the cookie, and rotation means
 * only the first would succeed — the rest would log the parent out mid-read.
 */
let inFlightRefresh: Promise<string | null> | null = null;

/**
 * The bootstrap attempt, kept so it happens once per page load.
 *
 * React 18's StrictMode runs effects twice in development, and a second bootstrap
 * would present a cookie the first one had already rotated and blacklisted — a
 * session that signs itself out, in dev only, which is a memorable afternoon.
 */
let bootstrapped: Promise<void> | null = null;

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  status: 'loading',
  user: null,
  parentVerifiedUntil: null,

  isAuthenticated: () => get().accessToken !== null,

  isParentVerified: () => {
    const until = get().parentVerifiedUntil;
    return until !== null && until > Date.now();
  },

  signup: async (payload) => {
    const result = await api.signup(payload);
    set({ accessToken: result.access, status: 'authenticated', user: result.user });
  },

  login: async (email, password) => {
    const tokens = await api.login(email, password);
    set({ accessToken: tokens.access, status: 'authenticated' });
  },

  logout: async () => {
    // **Tell the server while we still can, then clear.** This used to clear
    // locally first, on the reasoning that whatever the server says this device
    // is signed out. The reasoning was right and the order was fatal: with the
    // access token already gone the logout arrived unauthenticated, the server
    // answered 401, and `client.request` did what it does with a 401 — refreshed
    // silently from the cookie it was being asked to revoke, marking the store
    // authenticated again. Sign out, pass the adult gate, and you were back in.
    //
    // The `finally` keeps the original property: a failed blacklist still leaves
    // this device signed out rather than stranding a parent in a half-signed-out
    // UI. What it no longer does is leave the *session* alive.
    try {
      await api.logout();
    } catch {
      // Offline, or a token the server had already invalidated. Either way the
      // local half below is what this device can still guarantee.
    } finally {
      set({ accessToken: null, status: 'anonymous', user: null, parentVerifiedUntil: null });
    }
  },

  refresh: async () => {
    if (inFlightRefresh) return inFlightRefresh;

    const spendCookie = async (): Promise<string> => {
      const tokens = await api.refresh();
      // The rotated refresh token arrives as a Set-Cookie; there is nothing for
      // us to store, which is the entire benefit.
      set({ accessToken: tokens.access, status: 'authenticated' });
      return tokens.access;
    };

    inFlightRefresh = spendCookie()
      .catch(async (caught: unknown) => {
        // **Only a 401 is an answer about the session.** A 429, a 502 or a dropped
        // connection means we do not know yet, and treating "do not know" as "you
        // are signed out" is how a server hiccup evicts a parent from a session
        // that was still valid — then sends them to a login form to fix a password
        // that was never wrong.
        if (!(caught instanceof ApiError && caught.status === 401)) {
          // A cold start has no token to preserve either way, and holding
          // `loading` forever would leave `ProtectedRoute` on a blank screen.
          if (get().status === 'loading') set({ status: 'anonymous' });
          return null;
        }

        // **A 401 might be a rotation race rather than the end of the session.**
        // `ROTATE_REFRESH_TOKENS` and `BLACKLIST_AFTER_ROTATION` are both on, so
        // spending the cookie invalidates it — and `inFlightRefresh` only
        // deduplicates *within one tab*. Open the dashboard and the child's story
        // side by side and both bootstrap at load: one wins, the other presents a
        // cookie that was alive a moment ago and gets signed out. That is not a
        // developer's problem with three tabs open; it is a parent who has the
        // dashboard open handing the tablet to their child, and §14.5 says the
        // child must never land on a login form.
        //
        // The winner has already put a fresh cookie in the jar this tab shares,
        // so trying exactly once more is usually enough. The wait is for that
        // `Set-Cookie` to land. If the second attempt also 401s the session
        // really is over, and one extra request is a cheap price for not evicting
        // a family mid-story.
        await new Promise((resolve) => setTimeout(resolve, REFRESH_RACE_RETRY_MS));
        try {
          return await spendCookie();
        } catch (second: unknown) {
          if (second instanceof ApiError && second.status === 401) {
            set({ accessToken: null, status: 'anonymous', user: null });
          } else if (get().status === 'loading') {
            set({ status: 'anonymous' });
          }
          return null;
        }
      })
      .finally(() => {
        inFlightRefresh = null;
      });

    return inFlightRefresh;
  },

  bootstrap: () => {
    bootstrapped ??= get()
      .refresh()
      .then(() => {
        // `refresh` already set the outcome. This only covers the case where it
        // resolved from an in-flight call that predates the bootstrap.
        if (get().status === 'loading') set({ status: 'anonymous' });
      });
    return bootstrapped;
  },

  setUser: (user) => set({ user }),

  passParentGate: () => set({ parentVerifiedUntil: Date.now() + PARENT_GATE_TTL_MS }),

  clearParentGate: () => set({ parentVerifiedUntil: null }),
}));

// Hand the API client its two callbacks. Done once, at module load, so no
// component has to remember to wire it up.
connectAuth({
  accessToken: () => useAuthStore.getState().accessToken,
  refresh: () => useAuthStore.getState().refresh(),
  onSessionLost: () =>
    useAuthStore.setState({
      accessToken: null,
      status: 'anonymous',
      user: null,
      parentVerifiedUntil: null,
    }),
});

/** Test seam: forget that the bootstrap ran. Never called by the app. */
export function resetAuthBootstrapForTests(): void {
  bootstrapped = null;
  inFlightRefresh = null;
}
