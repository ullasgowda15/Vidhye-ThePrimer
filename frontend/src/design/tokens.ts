/**
 * Typed mirror of tokens.css (PRODUCT.md §15.11).
 *
 * The CSS variables stay the single runtime source of truth — this object
 * exists so TypeScript can name a token (charts, canvas, generated SVG,
 * anywhere a raw value is unavoidable) without anyone hardcoding a hex.
 * Values here reference `var(--…)` rather than duplicating hex codes, so a
 * theme swap can never leave the two out of sync.
 */

export const themes = ['storyland', 'calm', 'night'] as const;
export type ThemeName = (typeof themes)[number];

/** `data-theme` value for a theme. Storyland is `:root`, so it has none. */
export const themeAttribute: Record<ThemeName, string | undefined> = {
  storyland: undefined,
  calm: 'calm',
  night: 'night',
};

export const color = {
  bg: 'var(--bg)',
  surface: 'var(--surface)',
  primary: 'var(--primary)',
  accent: 'var(--accent)',
  success: 'var(--success)',
  text: 'var(--text)',
  textMuted: 'var(--text-muted)',
  border: 'var(--border)',
  calmNightBg: 'var(--calm-night-bg)',
  calmNightSurface: 'var(--calm-night-surface)',
  calmNightText: 'var(--calm-night-text)',
} as const;

/** 8pt grid: 4, 8, 12, 16, 24, 32, 48, 64, 96, 128. */
export const space = {
  1: 'var(--space-1)',
  2: 'var(--space-2)',
  3: 'var(--space-3)',
  4: 'var(--space-4)',
  5: 'var(--space-5)',
  6: 'var(--space-6)',
  7: 'var(--space-7)',
  8: 'var(--space-8)',
  9: 'var(--space-9)',
  10: 'var(--space-10)',
} as const;

export const fontSize = {
  1: 'var(--font-size-1)',
  2: 'var(--font-size-2)',
  3: 'var(--font-size-3)',
  4: 'var(--font-size-4)',
  5: 'var(--font-size-5)',
  6: 'var(--font-size-6)',
  7: 'var(--font-size-7)',
  8: 'var(--font-size-8)',
} as const;

export const fontFamily = {
  display: 'var(--font-display)',
  text: 'var(--font-text)',
} as const;

/** Durations only — easing is `--motion-ease`. All respect prefers-reduced-motion. */
export const motion = {
  fast: 'var(--motion-fast)',
  base: 'var(--motion-base)',
  slow: 'var(--motion-slow)',
  ease: 'var(--motion-ease)',
} as const;

export const radius = {
  sm: 'var(--radius-sm)',
  md: 'var(--radius-md)',
  lg: 'var(--radius-lg)',
  full: 'var(--radius-full)',
} as const;

export const shadow = { card: 'var(--shadow-card)' } as const;

/** Minimum interactive target. ~64px on the child surface (§15.4). */
export const targetMin = 'var(--target-min)';

export const tokens = {
  color,
  space,
  fontSize,
  fontFamily,
  motion,
  radius,
  shadow,
  targetMin,
} as const;

export type Tokens = typeof tokens;
