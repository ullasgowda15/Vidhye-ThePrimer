/**
 * Making the first sound actually play.
 *
 * Every voice in the child surface starts from a `useEffect` — the audio URL
 * arrives on the stream, and the app calls `play()`. That is a *programmatic*
 * call, and browsers refuse programmatic playback of audible media until the
 * document has been touched (Chrome calls it sticky activation; Safari is
 * stricter still and scopes it to the element).
 *
 * On a normal walk through the app there is always a tap first — a star on the
 * child picker — and the greeting plays. Land on `/welcome/:childId` or
 * `/story/:childId` directly, though, and there has been no tap in that
 * document: a reload, a bookmark, a PWA launch from the home screen. The
 * greeting is refused, the rejection was being swallowed, and a pre-reader got a
 * silent screen with nothing on it explaining why. Once they touched anything,
 * activation was granted and every later beat spoke — which is exactly the
 * reported shape: *silent at the beginning, fine once engaged*.
 *
 * Two mechanisms, because neither is sufficient alone:
 *
 *   `primeAudioOnFirstGesture` — the fix for the common case. The first
 *   pointer or key event anywhere in the app plays a fraction of a second of
 *   silence through a real `<audio>` element. That is a play *inside* a gesture
 *   handler, so it is always allowed, and it is what converts the document to
 *   "the user has interacted here" for everything that follows.
 *
 *   `playOrReportBlocked` — the fallback, for when even that has not happened.
 *   It reports whether the browser refused, so a screen can put something on
 *   the glass instead of pretending it is speaking.
 *
 * Neither is a workaround for a policy we disagree with. A child should not have
 * to know that a browser has rules; the tap they have already made should be
 * enough, and when it has not happened yet they should be *told* to tap, in a
 * way that works without reading.
 */

/** A hair of silence — enough to count as playback, too short to be heard. */
const SILENT_WAV =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=';

let primed = false;

/**
 * Grant this document audio permission on the first thing the user touches.
 *
 * Idempotent and self-removing: it costs one listener until the first gesture
 * and nothing afterwards. Returns a cleanup so a caller in a React effect can
 * detach it on unmount without leaving the app permanently un-primed — calling
 * this again re-arms it.
 */
export function primeAudioOnFirstGesture(): () => void {
  if (typeof window === 'undefined' || primed) return () => undefined;

  const unlock = () => {
    if (primed) return;
    primed = true;
    detach();
    try {
      const silence = new Audio(SILENT_WAV);
      silence.volume = 0;
      const started = silence.play();
      // Swallowed on purpose. If even a gesture-scoped play is refused there is
      // nothing further to try, and `playOrReportBlocked` will surface it on the
      // screen that actually wanted to speak.
      if (started && typeof started.then === 'function') {
        started.then(
          () => undefined,
          () => undefined,
        );
      }
    } catch {
      // Same reasoning. Never let priming throw into a gesture handler.
    }
  };

  // `pointerdown` rather than `click`: it fires at the start of a touch, so the
  // unlock is done before the tap it belongs to has finished being a tap.
  // `keydown` is there for a parent driving the app from a keyboard.
  const events: Array<keyof WindowEventMap> = ['pointerdown', 'keydown'];
  const detach = () => {
    for (const name of events) window.removeEventListener(name, unlock);
  };
  for (const name of events) {
    window.addEventListener(name, unlock, { once: true, passive: true });
  }

  return detach;
}

/** Has a gesture already unlocked audio in this document? */
export function audioIsPrimed(): boolean {
  return primed;
}

/**
 * Start an audio element, and say whether the browser allowed it.
 *
 * Resolves `true` when the sound is playing and `false` when it was refused.
 * **Never rejects** — a screen in the child surface has nothing useful to do
 * with an exception, and §13 is explicit that a child is never shown a failure.
 *
 * The `false` is the whole point of this function existing. The previous code
 * mapped both outcomes to `undefined`, so the app could not distinguish "the
 * companion is speaking" from "the companion has been silenced", and could not
 * offer the tap that would fix it.
 */
export async function playOrReportBlocked(element: HTMLAudioElement): Promise<boolean> {
  try {
    const started = element.play();
    // Older engines return undefined rather than a promise. Treat that as
    // success: they predate the autoplay policies this module exists for.
    if (started && typeof started.then === 'function') {
      await started;
    }
    return true;
  } catch {
    return false;
  }
}
