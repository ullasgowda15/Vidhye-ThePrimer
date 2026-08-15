/**
 * What a child sees when the browser has refused to let the companion speak.
 *
 * Only ever rendered after a `play()` was actually declined — never as a
 * decoration and never speculatively. On every ordinary visit
 * (`lib/audioUnlock.primeAudioOnFirstGesture`) this does not appear at all.
 *
 * Three constraints shaped it, and they rule out most of what a grown-up app
 * would do here:
 *
 * **No words.** The audience cannot read; that is the entire premise of the
 * product (§14.5). "Tap to play audio" is a sentence aimed at the parent, on a
 * screen the child is looking at. So the cue is a speaker with sound coming out
 * of it, which is the one icon a four-year-old already knows from every other
 * screen in their life.
 *
 * **Not an error.** Nothing red, nothing with a warning shape, no apology. From
 * the child's side this is not a failure at all — it is the companion waiting to
 * be greeted, and §13 forbids showing them a fault they cannot act on. The
 * motion is a slow breath rather than a nag.
 *
 * **The whole screen is the target, not this.** The caller puts a tap handler on
 * its container; this is only the thing that makes the invitation visible. A
 * small button would be a small target for an imprecise finger, and missing it
 * would teach the child that tapping does not work — worse than no cue.
 */
export function TapToHear({ companionName }: { companionName?: string }) {
  return (
    <div
      data-testid="tap-to-hear"
      // `pointer-events-none` so every pixel of this belongs to the container's
      // handler. Nothing here should be able to swallow the tap it is asking for.
      className="pointer-events-none mt-6 flex flex-col items-center gap-3"
      // The invitation, for a screen reader and for a parent who has turned one
      // on. `role="status"` rather than an alert: it is news, not a problem.
      role="status"
      aria-label={
        companionName ? `Tap anywhere to hear ${companionName}` : 'Tap anywhere to hear the story'
      }
    >
      <span className="relative flex h-20 w-20 items-center justify-center">
        {/* The slow pulse. `motion-safe:` so a child who has asked the OS for
            less motion gets a still icon rather than a moving one — the cue
            still reads, it just does not breathe. */}
        <span className="absolute inset-0 rounded-full bg-accent/20 motion-safe:animate-[breathe_3s_ease-in-out_infinite]" />
        <svg
          viewBox="0 0 24 24"
          className="relative h-10 w-10 text-accent"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          {/* A speaker, then two arcs of sound. Drawn rather than an asset so it
              is available before any network request has finished — the moment
              this is needed is precisely the moment nothing else has loaded. */}
          <path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" stroke="none" />
          <path d="M15.5 8.5a5 5 0 0 1 0 7" />
          <path d="M18.5 5.5a9 9 0 0 1 0 13" />
        </svg>
      </span>
    </div>
  );
}
