/**
 * The seam between one beat and the next (PRODUCT.md §4.9, §14.7, §15.8).
 *
 * §4.9 calls latency-hiding a **core UX mechanic**, not a nicety, and names the
 * mechanism: "a narrative beat masks any generation latency — *Pip leads you down
 * the mossy path…* while the next scene renders." This component is that
 * sentence, implemented. It owns the whole gap between the child tapping a card
 * and the consequence appearing, and there is deliberately no code path through
 * it that produces a spinner, a progress bar, or the word "loading".
 *
 * Three states, which are the three things that are actually true:
 *
 * * **settled** — a scene is on screen and nothing is happening. No motion at all
 *   beyond the companion's own idle breathing, because calm is the default and a
 *   screen that is always moving is a screen a four-year-old cannot rest in.
 * * **pending** — the child has acted and the next beat is generating. A band of
 *   light travels across the frame (`path-walk`) and a line of *story* appears
 *   underneath. The story line is the load-bearing part: it is the difference
 *   between waiting and being told what is happening.
 * * **turning** — a new beat has arrived. The outgoing scene lifts away
 *   (`page-leave`) while the incoming one settles in over it (`page-turn`), so
 *   there is never a frame in which the picture area is empty. That gap — a flash
 *   back to the thinking companion between two perfectly good scenes — is the
 *   "blank flash" this exists to remove.
 *
 * **Reduced motion is a real branch, not a decoration.** Under `reduce` the
 * outgoing scene is never mounted at all, rather than mounted and animated to
 * zero: its removal is driven by `animationend`, and an animation that does not
 * run never ends. Getting this wrong leaves the *previous* beat's picture sitting
 * on top of the current one permanently — a worse failure for the person who
 * asked for less motion than the motion was. Everything else collapses through
 * `motion-safe:` and the zeroed `--motion-*` tokens, and the flow is identical:
 * same frames, same order, no spinner.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';

import { usePrefersReducedMotion } from '@/lib/motion';

/**
 * Which transition to play. Both are §14.7's own examples; they differ in what
 * they imply about the story, not in how long they take.
 */
export type SceneVariant = 'page-turn' | 'path-walk';

export interface SceneTransitionProps {
  /**
   * Identity of the scene on screen — the beat id. A change is what plays the
   * transition, so it must be stable across the media frames that arrive within
   * one beat, and must change exactly once per beat.
   */
  sceneKey: string;
  /** Is the next beat being generated right now? Covers the gap with motion. */
  pending?: boolean;
  /**
   * A line of story to show while `pending` (§4.9). Never a status message — if
   * there is nothing story-shaped to say, pass nothing and the motion carries it
   * alone.
   */
  caption?: string | null;
  variant?: SceneVariant;
  children: ReactNode;
}

/** How long the outgoing scene is kept if `animationend` never fires. */
const LEAVE_FALLBACK_MS = 900;

export default function SceneTransition({
  sceneKey,
  pending = false,
  caption = null,
  variant = 'page-turn',
  children,
}: SceneTransitionProps) {
  const reducedMotion = usePrefersReducedMotion();

  /**
   * The scene we are turning away from, held only for the length of the turn.
   *
   * A snapshot of the rendered children rather than a URL, so this works for
   * whatever the scene happens to be — an illustration, or the companion
   * thinking. Kept in state rather than a ref because it has to survive the
   * re-renders that every arriving `media` frame causes: a ref-based version
   * cleared itself on the next render and the page-turn played over nothing.
   */
  const [leaving, setLeaving] = useState<{ key: string; node: ReactNode } | null>(null);
  const shown = useRef<{ key: string; node: ReactNode } | null>(null);

  useEffect(() => {
    const previous = shown.current;
    shown.current = { key: sceneKey, node: children };

    if (!previous || previous.key === sceneKey) return;
    // Under reduced motion nothing would ever clear this, so it is never set.
    // The new scene simply replaces the old one, instantly and completely.
    if (reducedMotion) return;
    setLeaving(previous);
    // Note the dependency list: `children` is deliberately absent. It changes on
    // every media frame within one beat, and reacting to that would restart the
    // page-turn each time the illustration or a card icon landed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneKey, reducedMotion]);

  // A belt-and-braces clear. `animationend` is the primary path, but an element
  // that is display:none when its animation would have started (a backgrounded
  // tab) never fires one, and the cost of missing it is the previous beat's
  // picture stuck over the current one.
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => setLeaving(null), LEAVE_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [leaving]);

  return (
    <div
      className="relative isolate w-full"
      data-testid="scene-transition"
      data-variant={variant}
      data-pending={pending || undefined}
    >
      {leaving ? (
        <div
          aria-hidden="true"
          data-testid="scene-leaving"
          onAnimationEnd={() => setLeaving(null)}
          className="pointer-events-none absolute inset-0 z-0 motion-safe:animate-[page-leave_var(--motion-slow)_var(--motion-ease)_forwards]"
        >
          {leaving.node}
        </div>
      ) : null}

      <div
        // Keyed on the beat, so React mounts a fresh element per scene and the
        // entry animation actually re-runs. Without the key the same node is
        // reused, the animation is considered already played, and the new scene
        // appears with a hard cut.
        key={sceneKey}
        data-testid="scene-current"
        className="relative z-10 motion-safe:animate-[page-turn_var(--motion-slow)_var(--motion-ease)_both]"
      >
        {children}
      </div>

      {pending ? <PathWalk caption={caption} /> : null}
    </div>
  );
}

/**
 * The gap, dressed as travel.
 *
 * A band of warm light crossing the frame, plus a line of story. It is over the
 * scene the child is already looking at, which is the point — they are not
 * waiting at a blank screen, they are watching their companion set off.
 *
 * `aria-hidden` on the light and a polite live region on the words: a screen
 * reader should hear "Pip leads you down the mossy path" once, and should never
 * be told about a moving gradient (§14.6).
 */
function PathWalk({ caption }: { caption: string | null }) {
  return (
    <>
      <div
        aria-hidden="true"
        data-testid="scene-pending"
        className="pointer-events-none absolute inset-0 z-20 overflow-hidden rounded-lg"
      >
        <div className="h-full w-1/3 bg-gradient-to-r from-transparent via-surface to-transparent opacity-0 motion-safe:opacity-60 motion-safe:animate-[path-walk_calc(var(--motion-slow)*3)_var(--motion-ease)_infinite]" />
      </div>
      {caption ? (
        <p
          data-testid="scene-caption"
          aria-live="polite"
          className="font-display text-3 relative z-20 mt-4 text-text-muted"
        >
          {caption}
        </p>
      ) : null}
    </>
  );
}
