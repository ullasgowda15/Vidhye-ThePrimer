/**
 * Reading the reduced-motion preference (PRODUCT.md §14.6, §14.7, §15.8).
 *
 * Most of this product's reduced-motion support is free and stays free: every
 * animation is applied through Tailwind's `motion-safe:` variant, which compiles
 * to `@media (prefers-reduced-motion: no-preference)`, and `tokens.css` zeroes
 * the `--motion-*` durations under `reduce`. Nothing has to ask.
 *
 * This hook exists for the one case that cannot be expressed in CSS: state that
 * is only correct *because* an animation is going to run. `SceneTransition` holds
 * the outgoing scene on screen and clears it on `animationend` — and with motion
 * disabled there is no animation, so no `animationend`, so the outgoing scene
 * would sit on top of the story forever. A component that mounts an element it
 * expects an animation to clean up has to know whether that animation exists.
 *
 * Subscribed rather than read once: the preference is an OS-level toggle a parent
 * may flip mid-session, on a device shared with someone who needs it.
 */

import { useEffect, useState } from 'react';

export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

/**
 * Does this person want motion kept to a minimum?
 *
 * Defaults to `false` — motion allowed — when `matchMedia` is unavailable, which
 * is jsdom and some older embedded webviews. False is the safe default here and
 * the direction matters: a component that wrongly believes motion is *off* skips
 * an animation and looks plain, while one that wrongly believes it is *on* waits
 * for an `animationend` that never comes and leaves an element stuck on screen.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => matches());

  useEffect(() => {
    const query = window.matchMedia?.(REDUCED_MOTION_QUERY);
    if (!query) return;

    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    // `addEventListener` on a MediaQueryList is the modern spelling; Safari
    // before 14 only has `addListener`. Both are handled because this runs on a
    // family tablet, not a developer's laptop.
    if (query.addEventListener) {
      query.addEventListener('change', onChange);
      return () => query.removeEventListener('change', onChange);
    }
    query.addListener(onChange);
    return () => query.removeListener(onChange);
  }, []);

  return reduced;
}

function matches(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

/**
 * Lines to cover a gap with (§4.9). Story, never status.
 *
 * §15.8 lists "loading-as-narration" as a motion category alongside page
 * transitions, which is why this sits next to the reduced-motion hook rather than
 * inside the component: it is the half of latency-hiding that survives when the
 * motion itself is switched off. A child who has asked for no animation still
 * gets told what their companion is doing.
 *
 * Written as things the *companion* is doing, because that is what makes the wait
 * part of the story instead of an interruption to it. Deliberately open-ended —
 * none of them promises what the next scene contains, so none of them can be
 * contradicted by the beat that arrives.
 */
const WAITING_LINES = [
  (name: string) => `${name} leads you down the mossy path…`,
  (name: string) => `${name} looks back to check you are following…`,
  (name: string) => `${name} pushes a branch aside and peeks through…`,
  (name: string) => `${name} hums a little as you walk…`,
] as const;

/**
 * One waiting line, chosen by the beat it follows rather than at random.
 *
 * Deterministic on purpose: a random line would change on every re-render, and
 * the story surface re-renders whenever a media frame lands — so the child would
 * watch the sentence rewrite itself four times while they waited.
 */
export function waitingLine(companionName: string, seed: number): string {
  const index = Math.abs(Math.trunc(seed)) % WAITING_LINES.length;
  return WAITING_LINES[index](companionName);
}
