/**
 * Parent session state (PRODUCT.md §18.2, §14.2).
 *
 * **Tokens live in memory only.** Not localStorage, not a readable cookie: an
 * XSS on a page that shows a child's memories should not also hand over a
 * fourteen-day refresh token. The cost is that a hard reload signs the parent
 * out, which is the right trade while the parent surface is a few short
 * sessions; the endgame is an httpOnly, SameSite refresh cookie set by the
 * backend, and that change lands entirely inside this file.
 *
 * The parent gate (§14.2) also lives here, because it is session state rather
 * than server state: it is a short-lived "an adult is holding the tablet" flag,
 * not a claim about identity, and it deliberately does *not* survive a reload.
 */

import { create } from 'zustand';

import { api, connectAuth, type Me, type User } from '@/lib/api/client';

/** How long one adult challenge keeps the parent area open. */
export const PARENT_GATE_TTL_MS = 15 * 60 * 1000;

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: User | Me | null;
  /** Epoch ms until which the adult challenge counts as passed. */
  parentVerifiedUntil: number | null;

  isAuthenticated: () => boolean;
  isParentVerified: () => boolean;

  signup: (payload: { email: string; password: string; display_name?: string }) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Rotate the pair. Returns the new access token, or null if the session is over. */
  refresh: () => Promise<string | null>;
  setUser: (user: Me) => void;
  passParentGate: () => void;
  clearParentGate: () => void;
}

/**
 * In-flight refresh, shared by every caller.
 *
 * A dashboard fires several queries at once; when the access token expires they
 * all 401 together. Without this, each would spend the refresh token, and
 * rotation means only the first would succeed — the rest would log the parent
 * out mid-read.
 */
let inFlightRefresh: Promise<string | null> | null = null;

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  refreshToken: null,
  user: null,
  parentVerifiedUntil: null,

  isAuthenticated: () => get().accessToken !== null,

  isParentVerified: () => {
    const until = get().parentVerifiedUntil;
    return until !== null && until > Date.now();
  },

  signup: async (payload) => {
    const result = await api.signup(payload);
    set({ accessToken: result.access, refreshToken: result.refresh, user: result.user });
  },

  login: async (email, password) => {
    const tokens = await api.login(email, password);
    set({ accessToken: tokens.access, refreshToken: tokens.refresh });
  },

  logout: async () => {
    const { refreshToken } = get();
    // Clear locally first: whatever the server says, this device is signed out.
    set({ accessToken: null, refreshToken: null, user: null, parentVerifiedUntil: null });
    if (refreshToken) {
      // Best effort — a failed blacklist must not strand the parent in a
      // half-signed-out UI.
      await api.logout(refreshToken).catch(() => undefined);
    }
  },

  refresh: async () => {
    if (inFlightRefresh) return inFlightRefresh;

    const { refreshToken } = get();
    if (!refreshToken) return null;

    inFlightRefresh = api
      .refresh(refreshToken)
      .then((tokens) => {
        // Rotation issues a new refresh token; keeping the old one would blacklist us.
        set({ accessToken: tokens.access, refreshToken: tokens.refresh ?? refreshToken });
        return tokens.access;
      })
      .catch(() => {
        set({ accessToken: null, refreshToken: null, user: null });
        return null;
      })
      .finally(() => {
        inFlightRefresh = null;
      });

    return inFlightRefresh;
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
      refreshToken: null,
      user: null,
      parentVerifiedUntil: null,
    }),
});
