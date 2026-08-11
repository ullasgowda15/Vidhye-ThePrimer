/**
 * Per-world palettes (PRODUCT.md §15.1, §8.3).
 *
 * §15.1 asks for palettes that "shift with each world's mood — the Whispering
 * Woods is different from the Star Harbor — to reinforce place and emotion", and
 * §8.3 is what decides which world a child is in. This module is the seam between
 * them: the backend picks a world (`apps/story/worlds.py`), sends its key with the
 * adventure, and the key becomes `data-world` on the document — after which the
 * whole design system is a different colour and not one component knew about it.
 *
 * **Why an attribute and CSS variables rather than a theme object in React.**
 * Because the palette has to reach places React does not render: the `body`
 * background behind the scene, the focus ring in `index.css`, the SVG that stands
 * in for a companion portrait. A token override on the root element is the only
 * mechanism all three read, and it is the mechanism §15.11 already established for
 * Storyland/Calm/night. A world is not a new kind of theme; it is the same kind,
 * one layer in.
 *
 * **The keys are shared with the backend and must stay shared.** A world the
 * stylesheet has never heard of renders as the base theme, silently — which is why
 * `isWorld` exists and why `worldKeys` is asserted against the API's own list in
 * `worlds.test.ts`. This is a string crossing the stack; the test is the contract.
 *
 * **Night is a variant of each world, not a replacement for it.** §15.1 wants an
 * evening session to be low-stimulation, and the wrong way to do that is to throw
 * the world away and go grey: a child in the Coral Deep at bedtime should still be
 * in the Coral Deep, dimmer. So every world has two palettes and `data-theme
 *="night"` selects the second (`tokens.css`).
 */

import { useEffect } from 'react';

/** The worlds the stylesheet has a palette for. Mirrors `apps/story/worlds.py`. */
export const worldKeys = [
  'whispering_woods',
  'star_harbor',
  'coral_deep',
  'sunlit_canyon',
] as const;

export type WorldKey = (typeof worldKeys)[number];

/**
 * Display names, for the adult in the room and for assistive technology.
 *
 * Deliberately *not* what the child is told: nothing on the child surface is read,
 * and where they are is said by the companion in the narration (§14.6). These
 * appear in the story page's accessible label and nowhere else.
 */
export const worldNames: Record<WorldKey, string> = {
  whispering_woods: 'the Whispering Woods',
  star_harbor: 'Star Harbor',
  coral_deep: 'the Coral Deep',
  sunlit_canyon: 'the Sunlit Canyon',
};

/** Is this string one of the palettes we actually ship? */
export function isWorld(value: string | null | undefined): value is WorldKey {
  return typeof value === 'string' && (worldKeys as readonly string[]).includes(value);
}

/**
 * The `data-world` value for a key, or `undefined` for anything we cannot paint.
 *
 * Undefined rather than a default world on purpose. An adventure planned before
 * per-world palettes existed, or one whose world we have since renamed, should
 * fall back to the base Storyland tokens — which are complete and warm — rather
 * than be painted in a world the story is not set in. A wrong palette is worse than
 * no palette: it tells the child they are somewhere they are not.
 */
export function worldAttribute(key: string | null | undefined): WorldKey | undefined {
  return isWorld(key) ? key : undefined;
}

/**
 * The hour after which an evening session gets the night palette, and the hour it
 * ends (§15.1's "nighttime/bedtime-friendly").
 *
 * Local time, from the device clock, because that is what "evening" means to the
 * family holding it. Seven is when this age group's bedtime routine starts; six in
 * the morning is early enough that a child awake before their parents is not shown
 * a bright screen.
 */
export const NIGHT_FROM_HOUR = 19;
export const NIGHT_UNTIL_HOUR = 6;

/** Is it evening where the child is? */
export function isNightTime(at: Date = new Date()): boolean {
  const hour = at.getHours();
  return hour >= NIGHT_FROM_HOUR || hour < NIGHT_UNTIL_HOUR;
}

/**
 * Wear this world for as long as the component is mounted.
 *
 * Set on `document.documentElement` rather than on the scene element, because the
 * palette has to cover everything the scene does not: the page background behind a
 * short beat, the overscroll area on a tablet, the focus ring. Removed on unmount,
 * so leaving the story for the parent surface leaves the world behind with it —
 * the parent's Calm theme is a different surface and must not inherit a child's
 * forest.
 *
 * `night` is passed in rather than read from the clock here so a caller can decide
 * once per beat instead of once per render, and so a test can state the hour
 * instead of waiting for one.
 */
export function useWorldTheme(world: string | null | undefined, night: boolean): void {
  useEffect(() => {
    const root = document.documentElement;
    const key = worldAttribute(world);

    if (key) root.setAttribute('data-world', key);
    else root.removeAttribute('data-world');

    if (night) root.setAttribute('data-theme', 'night');
    else root.removeAttribute('data-theme');

    return () => {
      root.removeAttribute('data-world');
      root.removeAttribute('data-theme');
    };
  }, [world, night]);
}
