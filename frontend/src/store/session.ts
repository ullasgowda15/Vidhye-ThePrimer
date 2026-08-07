/**
 * Local UI state (PRODUCT.md §16.1).
 *
 * Deliberately tiny: anything the server owns lives in TanStack Query, not
 * here. This store is for things that are true only on this device right
 * now — which theme is showing, whether audio is muted.
 *
 * Stub on Day 1; the real session/companion state arrives with the story loop.
 */

import { create } from 'zustand';

import type { ThemeName } from '@/design/tokens';

interface SessionState {
  theme: ThemeName;
  muted: boolean;
  setTheme: (theme: ThemeName) => void;
  setMuted: (muted: boolean) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  theme: 'storyland',
  muted: false,
  setTheme: (theme) => set({ theme }),
  setMuted: (muted) => set({ muted }),
}));
