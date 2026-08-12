/**
 * Per-world palettes (§15.1, §8.3) — Day 11 criterion 4.
 *
 * The criterion is "≥3 distinct world palettes render, matching world mood; the
 * night-friendly variant works", and the interesting word in it is *distinct*. A
 * theme layer that exists but re-points nothing would pass any test that merely
 * asserted the attribute was set, so these read the stylesheet itself and compare
 * the values — the failure being guarded against is a world that quietly renders in
 * the default palette, which nobody would notice until a child was looking at it.
 */

import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { isNightTime, isWorld, useWorldTheme, worldAttribute, worldKeys } from '@/design/worlds';

/**
 * The stylesheet as it is written, read from disk rather than imported.
 *
 * A `?raw` import goes through Vite's CSS pipeline first, and what comes back is
 * not reliably the bytes in the file — which is the one thing these tests need,
 * since the assertion is about what a *declaration block* contains.
 */
const tokensCss = readFileSync(resolve(process.cwd(), 'src/design/tokens.css'), 'utf8');

/** The custom properties declared inside `selector { … }`, as `{token: value}`. */
function block(selector: string): Record<string, string> {
  // Quote-agnostic: whether the attribute selectors are written with single or
  // double quotes is a formatter's decision, not this test's business.
  const pattern = selector
    .replace(/[[\]=]/g, (char) => `\\${char}`)
    .replace(/'([^']*)'/g, `['"]$1['"]`);
  const match = new RegExp(`${pattern}\\s*\\{([^}]*)\\}`).exec(tokensCss);
  if (!match) return {};
  return Object.fromEntries(
    match[1]
      .split(';')
      .map((line) => line.trim())
      .filter((line) => line.startsWith('--'))
      .map((line) => {
        const [token, ...rest] = line.split(':');
        return [token.trim(), rest.join(':').trim()];
      }),
  );
}

/** Relative luminance of a `#rrggbb` value, per WCAG 2.1. */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two `#rrggbb` values, 1:1 … 21:1. */
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

afterEach(() => {
  document.documentElement.removeAttribute('data-world');
  document.documentElement.removeAttribute('data-theme');
});

describe('the world palettes', () => {
  it('ships the same worlds the story engine does', () => {
    // Mirrors `apps/story/worlds.py::WORLDS`, which asserts the same list on its
    // side. This string crosses the stack and nothing enforces it automatically.
    expect([...worldKeys]).toEqual([
      'whispering_woods',
      'star_harbor',
      'coral_deep',
      'sunlit_canyon',
    ]);
  });

  it('gives every world its own palette, not just its own name', () => {
    const palettes = worldKeys.map((key) => block(`[data-world='${key}']`));

    for (const [index, palette] of palettes.entries()) {
      // The tokens a component actually reads (§15.11). A block that set only
      // `--accent` would be a "world" that looks like every other world.
      expect(Object.keys(palette), worldKeys[index]).toEqual(
        expect.arrayContaining([
          '--bg',
          '--surface',
          '--primary',
          '--accent',
          '--text',
          '--border',
        ]),
      );
    }

    for (const token of ['--bg', '--primary', '--accent', '--text']) {
      const values = palettes.map((palette) => palette[token]);
      expect(new Set(values).size, `${token} is shared between worlds`).toBe(worldKeys.length);
    }
  });

  it('keeps body text above WCAG AA in every world, day and night', () => {
    // §15.1's first rule, computed rather than eyeballed. `--text-muted` is the
    // token this test exists for: it is the only one in these blocks rendered at
    // body size (14–18px in the story footer, the waiting line, the home screen),
    // so it needs 4.5:1 — and tinting a grey toward a world's hue *lightens* it,
    // which is how three of the four first shipped between 3.87:1 and 4.48:1.
    // `--text` is checked alongside it because a world that dimmed its headline
    // colour would fail the same way and nobody would think to look.
    for (const key of worldKeys) {
      for (const [when, palette] of [
        ['day', block(`[data-world='${key}']`)],
        ['night', block(`[data-theme='night'][data-world='${key}']`)],
      ] as const) {
        for (const token of ['--text', '--text-muted'] as const) {
          const ratio = contrast(palette[token], palette['--bg']);
          expect(ratio, `${key} ${when} ${token} on --bg is ${ratio.toFixed(2)}:1`).toBeGreaterThan(
            4.5,
          );
        }
      }
    }
  });

  it('keeps every world legible after bedtime', () => {
    for (const key of worldKeys) {
      const day = block(`[data-world='${key}']`);
      const night = block(`[data-theme='night'][data-world='${key}']`);

      // Still that world — a dimmed Coral Deep, not a generic grey (§15.1).
      expect(Object.keys(night), key).toEqual(
        expect.arrayContaining(['--bg', '--surface', '--text', '--primary']),
      );
      expect(night['--bg'], key).not.toEqual(day['--bg']);
      // Light on dark rather than dark on light: the day text colour would be
      // unreadable on the night background, and swapping only the background is
      // the way that mistake gets made.
      expect(night['--text'], key).not.toEqual(day['--text']);
    }
  });
});

describe('choosing the palette', () => {
  it('paints nothing at all for a world it cannot paint', () => {
    // An adventure planned before per-world palettes existed, or a world we have
    // since renamed. Falling back to the complete base theme is right; painting a
    // child's screen in a world their story is not set in is not.
    expect(worldAttribute('coral_deep')).toBe('coral_deep');
    expect(worldAttribute('glimmering_tidepools')).toBeUndefined();
    expect(worldAttribute(undefined)).toBeUndefined();
    expect(isWorld('')).toBe(false);
  });

  it('turns the lights down in the evening and up in the morning', () => {
    const at = (hour: number) => new Date(2026, 7, 12, hour, 30);

    expect(isNightTime(at(15))).toBe(false);
    expect(isNightTime(at(19))).toBe(true);
    expect(isNightTime(at(23))).toBe(true);
    expect(isNightTime(at(3))).toBe(true);
    expect(isNightTime(at(6))).toBe(false);
  });
});

describe('wearing the world', () => {
  function Scene({ world, night }: { world?: string; night: boolean }) {
    useWorldTheme(world, night);
    return <p>a story</p>;
  }

  it('dresses the whole document, and undresses it on the way out', () => {
    // The root rather than the scene element, because the palette has to cover what
    // the scene does not: the page behind a short beat, the overscroll area, the
    // focus ring in `index.css`.
    const view = render(<Scene world="star_harbor" night={false} />);

    expect(screen.getByText('a story')).toBeTruthy();
    expect(document.documentElement.dataset.world).toBe('star_harbor');
    expect(document.documentElement.dataset.theme).toBeUndefined();

    view.rerender(<Scene world="star_harbor" night={true} />);
    expect(document.documentElement.dataset.theme).toBe('night');

    // Leaving the story leaves the world behind with it — the parent surface is a
    // different theme entirely and must not inherit a child's harbour.
    view.unmount();
    expect(document.documentElement.dataset.world).toBeUndefined();
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });
});
