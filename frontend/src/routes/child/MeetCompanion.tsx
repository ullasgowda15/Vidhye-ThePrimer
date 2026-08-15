/**
 * Meeting the companion — the imprint (PRODUCT.md §4.3, §14.5, §14.7).
 *
 * This is the child's first moment in the product, and it is not a menu. The
 * companion *arrives*: it breathes, it says the child's name out loud, and it
 * is glad they are here. Everything on this screen is spent on being seen.
 *
 * Three rules hold, and all three are the same rule (§13):
 *
 * - **No spinner.** While the portrait is still being painted, the scene shows
 *   the companion asleep and stirring. Waiting is narrated, not indicated.
 * - **No error.** A failed request, a lost token, a provider outage — the child
 *   sees a sleeping friend, never a message. The parent surface is where things
 *   are explained.
 * - **No reading required.** The greeting is spoken. The caption is there for
 *   accessibility and for the adult in the room, not because the child needs it.
 *
 * Audio autoplays where the browser allows it, and where it does not — which is
 * most tablets on a fresh page — the whole scene is one enormous tap target
 * that starts it. A tap is something a four-year-old does anyway.
 *
 * That tap target is armed **only until the greeting has been heard**, and the
 * distinction is the whole of it. A child explores a screen by touching it, so a
 * live whole-screen handler after the greeting has finished means the companion
 * restarts their one line every few seconds — the same sentence, forever, with
 * no way to avoid triggering it and nothing on screen to explain why. Repeating
 * is a fine thing to want; it just has to be asked for. So once the greeting has
 * started, the scene stops listening and replay moves onto its own control.
 *
 * The scene also ends somewhere. The imprint hands off to the first story
 * (§4.3 → §4.4): the star does here what it does on the home screen, so the
 * gesture a child has already learned is the one that works.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { TapToHear } from '@/components/TapToHear';
import { playOrReportBlocked } from '@/lib/audioUnlock';
import { useCompanion } from '@/lib/api/queries';

export default function MeetCompanion() {
  const { childId } = useParams();
  const navigate = useNavigate();
  const { data: companion } = useCompanion(childId);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  /** Has the greeting begun playing? Not "finished" — see `handleSceneTap`. */
  const [heard, setHeard] = useState(false);
  /** The browser refused to start the greeting. See `lib/audioUnlock`. */
  const [blocked, setBlocked] = useState(false);

  const audioUrl = companion?.greeting.audio_url ?? null;
  const ready = Boolean(companion?.assets_ready);

  /**
   * Start the greeting, swallowing every way that can fail.
   *
   * `play()` returns a promise that rejects when a browser refuses autoplay —
   * and on older engines returns nothing at all — so both shapes are handled.
   * Neither outcome may surface: the tap-to-begin affordance covers a refusal,
   * and a child is never shown a failure (§13).
   *
   * `force` is what the "again" control passes. Without it a call that lands
   * mid-sentence is ignored rather than starting the line over: per the media
   * spec `play()` on an element that has already ended seeks back to the top,
   * which is exactly what replay wants and exactly what a stray tap must not do.
   */
  function speak({ force = false }: { force?: boolean } = {}) {
    const element = audioRef.current;
    if (!element || !audioUrl) return;
    if (!force && !element.paused) return;
    // The rejection is the interesting outcome, and it used to be discarded:
    // both "speaking" and "the browser said no" arrived here as `undefined`, so
    // the screen could not tell the child which had happened. `blocked` is what
    // puts the cue on the glass (`TapToHear`).
    void playOrReportBlocked(element).then((playing) => setBlocked(!playing));
  }

  /**
   * The fallback for a browser that refused autoplay, and only that.
   *
   * Disarmed as soon as the greeting is under way, so touching the scene
   * afterwards does nothing at all.
   */
  function handleSceneTap() {
    if (heard) return;
    speak();
  }

  // Speak the moment the voice exists. Autoplay is the intent; the tap is the
  // fallback, not the plan.
  useEffect(() => {
    if (!audioUrl || heard) return;
    speak();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioUrl, heard]);

  return (
    <main
      onClick={handleSceneTap}
      className="min-h-full bg-bg text-text flex flex-col items-center justify-center p-6 text-center"
    >
      <div className="relative flex flex-col items-center">
        {ready && companion?.portrait_url ? (
          <img
            src={companion.portrait_url}
            alt={`${companion.name}, your companion`}
            data-testid="companion-portrait"
            className="w-[min(70vw,26rem)] rounded-lg shadow-card motion-safe:animate-[breathe_4s_ease-in-out_infinite]"
          />
        ) : (
          <SleepingFriend />
        )}
      </div>

      {/* Only when a `play()` was actually refused. The `<main>` tap handler is
          the target; this makes the invitation visible without a sentence the
          child cannot read (§14.5). */}
      {blocked && !heard ? <TapToHear companionName={companion?.name} /> : null}

      {ready && companion ? (
        <>
          <h1 className="font-display text-6 mt-6 text-primary">{companion.name}</h1>
          {/* The caption, not the message. The line the child hears is the audio. */}
          <p className="font-display text-4 mt-3 max-w-[34rem] text-text">
            {companion.greeting.text}
          </p>

          {/* Two glyphs, no words: the sun is this companion's hello and the
              star is a story, which is what it already means on the home screen.
              Whichever action is next carries the accent. */}
          <div className="mt-7 flex items-center justify-center gap-5">
            {audioUrl ? (
              <button
                type="button"
                // The whole scene is a tap target too, so the click is stopped
                // here rather than starting the greeting twice.
                onClick={(event) => {
                  event.stopPropagation();
                  speak({ force: true });
                }}
                aria-label={
                  heard
                    ? `Hear ${companion.name} say hello again`
                    : `Say hello to ${companion.name}`
                }
                data-testid="companion-greeting-replay"
                className={`min-h-target min-w-target rounded-full px-8 text-6 font-display shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95 ${
                  heard ? 'bg-surface text-primary' : 'bg-accent text-surface'
                }`}
              >
                ☀
              </button>
            ) : null}

            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                if (childId) navigate(`/story/${childId}`);
              }}
              aria-label={`Begin a story with ${companion.name}`}
              data-testid="begin-story"
              className={`min-h-target min-w-target rounded-full px-8 text-6 font-display shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95 ${
                heard || !audioUrl ? 'bg-accent text-surface' : 'bg-surface text-primary'
              }`}
            >
              ★
            </button>
          </div>
        </>
      ) : (
        <p className="font-display text-4 mt-7 max-w-[30rem] text-text-muted">
          Someone is waking up…
        </p>
      )}

      {audioUrl ? (
        <audio
          ref={audioRef}
          src={audioUrl}
          data-testid="companion-greeting-audio"
          preload="auto"
          // `onPlay` rather than the `play()` promise: the element is the thing
          // that knows, and it also covers a play the user started some other
          // way (native controls, a media key).
          onPlay={() => {
            setHeard(true);
            setBlocked(false);
          }}
          onEnded={() => setHeard(true)}
        />
      ) : null}

      {/* Keyframes live here rather than in the token sheet: this is one scene's
          idle motion, not a shared token. `motion-safe:` above is what makes it
          disappear entirely under prefers-reduced-motion (§14.6). */}
      <style>{`
        @keyframes breathe {
          0%, 100% { transform: scale(1) translateY(0); }
          50%      { transform: scale(1.02) translateY(-6px); }
        }
        @keyframes drift {
          0%, 100% { transform: translateY(0); opacity: 0.75; }
          50%      { transform: translateY(-10px); opacity: 1; }
        }
      `}</style>
    </main>
  );
}

/**
 * The waiting state: a curled, sleeping shape that stirs.
 *
 * The whole point is that it is *not* a loading indicator. A child looking at
 * this sees a friend who is not awake yet, which is a thing that happens to
 * friends — and if the art never arrives, this is a scene rather than a failure.
 */
function SleepingFriend() {
  return (
    <svg
      viewBox="0 0 240 200"
      className="w-[min(60vw,20rem)]"
      role="img"
      aria-label="Your companion is still sleeping"
    >
      <ellipse cx="120" cy="176" rx="86" ry="14" fill="var(--border)" opacity="0.6" />
      <g className="motion-safe:animate-[breathe_4s_ease-in-out_infinite]">
        <ellipse cx="120" cy="128" rx="76" ry="52" fill="var(--primary)" opacity="0.85" />
        <ellipse cx="120" cy="140" rx="48" ry="30" fill="var(--surface)" opacity="0.7" />
        <path
          d="M92 116 q 12 10 24 0"
          stroke="var(--text)"
          strokeWidth="4"
          fill="none"
          strokeLinecap="round"
        />
        <path
          d="M136 116 q 12 10 24 0"
          stroke="var(--text)"
          strokeWidth="4"
          fill="none"
          strokeLinecap="round"
        />
      </g>
      {[0, 1, 2].map((index) => (
        <text
          key={index}
          x={168 + index * 16}
          y={70 - index * 22}
          fontSize={16 + index * 6}
          fill="var(--text-muted)"
          className="motion-safe:animate-[drift_3s_ease-in-out_infinite]"
          style={{ animationDelay: `${index * 0.4}s` }}
        >
          z
        </text>
      ))}
    </svg>
  );
}
