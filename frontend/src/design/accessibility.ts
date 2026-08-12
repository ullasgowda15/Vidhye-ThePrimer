/**
 * Accessibility, applied to the child surface (PRODUCT.md §14.6, §7.13, §12.7).
 *
 * §14.6 asks for captions, adjustable audio, reduced motion, high contrast and a
 * dyslexia-friendly font. The parent sets them (`routes/parent/Settings`); this is
 * where they become true on the screen a child is looking at.
 *
 * **The same mechanism the world palettes use, one layer in.** Three of the five
 * flags are `data-a11y-*` attributes on the document root, and every visual
 * consequence lives in `tokens.css`. That is not a stylistic preference: reduced
 * motion has to reach the `body` background, the `@keyframes` in `index.css` and
 * the SVG companion, and high contrast has to re-point tokens that components read
 * without knowing a setting exists. A React context could not reach any of those,
 * and `design/worlds` already proved the attribute approach on exactly this
 * surface.
 *
 * The other two are not CSS at all and are handled by the components that own
 * them: `audio_volume` is a property of the `<audio>` element, and captions decide
 * whether the narration paragraph is rendered.
 *
 * **Reduced motion is a floor, never a ceiling.** The child tree already honours
 * the OS-level `prefers-reduced-motion` through Tailwind's `motion-safe:` on every
 * animated element, and `tokens.css` zeroes the duration tokens under that media
 * query. This flag adds the parent's own override on top with the identical
 * rules — so a child whose device does not expose the OS setting, or whose parent
 * wants stillness regardless of it, gets the same result. Neither can *re-enable*
 * motion the other switched off.
 */

import { useEffect } from 'react';

import type { Accessibility } from '@/lib/api/client';

/**
 * The defaults, matching `apps/profiles/settings.py::Accessibility`.
 *
 * Restated rather than derived from the API type, because they have to be right
 * while the settings request is still in flight — and a child's first beat must
 * not arrive silent because a fetch had not landed. Captions on and full volume
 * are the accessible answers; the three that change the experience for everyone
 * default off.
 */
export const DEFAULT_ACCESSIBILITY: Required<Accessibility> = {
  captions: true,
  reduced_motion: false,
  dyslexia_font: false,
  high_contrast: false,
  audio_volume: 1,
};

/** A partial blob from the API, filled in with the defaults above. */
export function withDefaults(settings: Accessibility | undefined): Required<Accessibility> {
  return { ...DEFAULT_ACCESSIBILITY, ...(settings ?? {}) };
}

/**
 * Will the child's screen carry the spoken words in text (§14.6, §7.13)?
 *
 * The rule has three parts and the order between them is the design:
 *
 * 1. `text_on` is the parent's taste about the story paragraph;
 * 2. `captions` is §14.6's guarantee that everything spoken has an on-screen
 *    equivalent, and it **outranks** the preference when they disagree;
 * 3. a silent voice outranks both, because a beat delivered as neither sound nor
 *    text is not a beat.
 *
 * A function rather than an expression inside the JSX because the same rule is
 * written down server-side in `ChildSettings.words_on_screen`, and a rule stated
 * in two places has to be testable in both. This is the half that renders.
 */
export function wordsOnScreen(
  settings: { text_on?: boolean; voice_on?: boolean; accessibility?: Accessibility } | undefined,
): boolean {
  const textOn = settings?.text_on ?? true;
  const voiceOn = settings?.voice_on ?? true;
  return textOn || withDefaults(settings?.accessibility).captions || !voiceOn;
}

/**
 * Volume, forced into the range `HTMLMediaElement.volume` will accept.
 *
 * An out-of-range assignment throws `IndexSizeError`, and a throw on this path
 * would be an uncaught error between a child and their story. The server clamps
 * too; this is the second lock, because the value also arrives from a cache that
 * may predate the clamp.
 */
export function safeVolume(value: number | undefined): number {
  if (typeof value !== 'number' || Number.isNaN(value)) return DEFAULT_ACCESSIBILITY.audio_volume;
  return Math.min(1, Math.max(0, value));
}

/**
 * Put the parent's accessibility choices on the document root.
 *
 * Attributes rather than classes so they compose with `data-theme` and
 * `data-world` in `tokens.css` at a predictable specificity, and so a reviewer can
 * see the whole accessibility state of the page in one line of the DOM inspector.
 *
 * Cleans up on unmount for the reason `useWorldTheme` does: these routes are
 * per-child, and a parent walking from one child's story to another's must not
 * carry the first child's settings with them.
 */
export function useAccessibility(settings: Accessibility | undefined): void {
  const applied = withDefaults(settings);
  const {
    reduced_motion: reducedMotion,
    dyslexia_font: dyslexia,
    high_contrast: contrast,
  } = applied;

  useEffect(() => {
    const root = document.documentElement;
    const flags: Array<[string, boolean]> = [
      ['data-a11y-motion', reducedMotion],
      ['data-a11y-font', dyslexia],
      ['data-a11y-contrast', contrast],
    ];

    for (const [name, on] of flags) {
      if (on) root.setAttribute(name, 'on');
      else root.removeAttribute(name);
    }

    return () => {
      for (const [name] of flags) root.removeAttribute(name);
    };
  }, [reducedMotion, dyslexia, contrast]);
}
