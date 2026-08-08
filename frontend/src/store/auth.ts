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
    // Clear locally first: whatever the server says, this device is signed out.
    // The cookie needs the server (it is httpOnly), so the call is still made —
    // but a failed blacklist must not strand the parent in a half-signed-out UI.
    set({ accessToken: null, status: 'anonymous', user: null, parentVerifiedUntil: null });
    await api.logout().catch(() => undefined);
  },

  refresh: async () => {
    if (inFlightRefresh) return inFlightRefresh;

    inFlightRefresh = api
      .refresh()
      .then((tokens) => {
        // The rotated refresh token arrives as a Set-Cookie; there is nothing for
        // us to store, which is the entire benefit.
        set({ accessToken: tokens.access, status: 'authenticated' });
        return tokens.access;
      })
      .catch((caught: unknown) => {
        // **Only a 401 is an answer about the session.** A 429, a 502 or a dropped
        // connection means we do not know yet, and treating "do not know" as "you
        // are signed out" is how a server hiccup evicts a parent from a session
        // that was still valid — then sends them to a login form to fix a password
        // that was never wrong.
        //
        // (The 401-retry path in `client.ts` still ends the session on any failed
        // refresh, and should: there we already have a 401 in hand, so the access
        // token is known dead rather than merely unconfirmed.)
        if (caught instanceof ApiError && caught.status === 401) {
          set({ accessToken: null, status: 'anonymous', user: null });
        } else if (get().status === 'loading') {
          // A cold start has no token to preserve either way, and holding
          // `loading` forever would leave `ProtectedRoute` on a blank screen.
          set({ status: 'anonymous' });
        }
        return null;
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
