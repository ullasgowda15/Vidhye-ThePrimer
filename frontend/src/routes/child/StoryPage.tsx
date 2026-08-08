/**
 * The story (PRODUCT.md §4.9, §14.5, §18.5).
 *
 * This screen has exactly one rule, and everything on it follows from that rule:
 * **there is never a spinner, and never an error.** A three-year-old cannot read
 * "something went wrong" and cannot interpret a rotating arc. So the gap between
 * tapping the star and the first illustrated, voiced scene is filled with the
 * companion *thinking* — a face they know, breathing, with a few drifting dots.
 * If the generation fails outright, that scene simply stays. Waiting is narrated,
 * never indicated (§14.7).
 *
 * Everything arrives progressively, in the order it becomes available (§18.5):
 *
 *   1. the `beat` frame → narration appears, and is spoken as soon as it can be
 *   2. the `media` frames → the illustration crossfades in; the audio starts
 *   3. `done` → the child is invited to answer
 *
 * The narration is never held back for the picture. That ordering is the whole
 * reason the backend streams instead of returning a finished beat.
 */

import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { useBeginAdventure, useCompanion } from '@/lib/api/queries';
import { consumeBeatStream, type BeatEvent } from '@/lib/sse';
import { useAuthStore } from '@/store/auth';

const collapse = (text: string) => text.split(/\s+/).join(' ').toLowerCase();

/**
 * The beat's question, but only when it is not already in the narration.
 *
 * `interaction.prompt_audio` is contracted to be the short question asked after
 * the narration, and it is captioned on its own line because it is the thing the
 * child has to answer. When a model fills it with the narration again instead, the
 * same sentences appear twice on the page — which is what a child and their parent
 * actually reported.
 *
 * The backend now normalises this away when it writes a beat, so this is the guard
 * for beats written before it did, and for a provider that finds a new way to
 * repeat itself. Cheap, and the alternative is a duplicate on a child's screen.
 */
function askedSeparately(beat: BeatEvent | null): string | null {
  const prompt = beat?.interaction?.prompt_audio?.trim();
  if (!prompt || !beat) return null;
  const narration = collapse(beat.narration);
  const asked = collapse(prompt);
  return narration.includes(asked) || asked.includes(narration) ? null : prompt;
}

export default function StoryPage() {
  const { childId } = useParams();
  const { data: companion } = useCompanion(childId);
  const { data: adventure } = useBeginAdventure(childId);
  const accessToken = useAuthStore((state) => state.accessToken);

  const [beat, setBeat] = useState<BeatEvent | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  /** Has the narration begun playing? Not "finished" — see `handleSceneTap`. */
  const [heard, setHeard] = useState(false);
  const [awaitingChoice, setAwaitingChoice] = useState(false);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const streamUrl = adventure?.streamUrl;

  useEffect(() => {
    if (!streamUrl) return;
    const controller = new AbortController();

    void consumeBeatStream(streamUrl, {
      accessToken,
      signal: controller.signal,
      onBeat: (event) => {
        setBeat(event);
        // A replayed beat (a reload mid-story) already carries its media.
        if (event.scene.imageUrl) setImageUrl(event.scene.imageUrl);
        if (event.audioUrl) setAudioUrl(event.audioUrl);
      },
      onMedia: (event) => {
        if (event.scene?.imageUrl) setImageUrl(event.scene.imageUrl);
        if (event.audioUrl) setAudioUrl(event.audioUrl);
      },
      onEnd: (done) => setAwaitingChoice(done?.status === 'awaiting_choice'),
    });

    return () => controller.abort();
    // `accessToken` is read once per stream on purpose: re-opening the stream
    // because a token rotated mid-beat would restart the narration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamUrl]);

  /**
   * Start the voice, swallowing every way that can fail.
   *
   * `play()` rejects when a browser refuses autoplay, and on older engines
   * returns nothing at all, so both shapes are handled. Neither outcome may
   * surface: the tap-anywhere affordance covers a refusal, and a child is never
   * shown a failure (§13).
   *
   * `force` is what the "again" control passes. Without it a call that lands
   * mid-sentence is ignored rather than restarting the beat: per the media spec
   * `play()` on an element that has ended seeks back to the top, which replay
   * wants and a stray tap must not have.
   */
  function speak({ force = false }: { force?: boolean } = {}) {
    const element = audioRef.current;
    if (!element || !audioUrl) return;
    if (!force && !element.paused) return;
    try {
      const started = element.play();
      if (started && typeof started.then === 'function') {
        started.then(
          () => undefined,
          () => undefined,
        );
      }
    } catch {
      // Nothing to do and nothing to say — the tap target stays on screen.
    }
  }

  /**
   * The fallback for a browser that refused autoplay, and only that.
   *
   * Disarmed once the narration is under way. A child explores a screen by
   * touching it, and a handler that stays armed turns every touch into the story
   * starting over from the first line.
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

  const companionName = companion?.name ?? adventure?.companion.name ?? 'Your friend';
  const question = askedSeparately(beat);

  return (
    <main
      onClick={handleSceneTap}
      className="min-h-full bg-bg text-text flex flex-col items-center justify-center p-6 text-center"
    >
      <div className="w-full max-w-[44rem]">
        {/* The picture's slot is reserved from the start so the narration does not
            jump down the page when the illustration lands. */}
        <div className="relative mx-auto flex min-h-[14rem] w-full items-center justify-center">
          {imageUrl ? (
            <img
              src={imageUrl}
              alt={beat?.scene.status === 'ready' ? 'The scene' : ''}
              data-testid="beat-illustration"
              className="w-full rounded-lg shadow-card motion-safe:animate-[fade-in_var(--motion-slow)_var(--motion-ease)]"
            />
          ) : (
            <ThinkingCompanion portraitUrl={companion?.portrait_url ?? null} name={companionName} />
          )}
        </div>

        {beat ? (
          <>
            {/* The caption, not the message: the line the child *hears* is the
                audio. It is on screen for the adult in the room and for
                accessibility (§14.6). */}
            <p
              data-testid="beat-narration"
              className="font-display text-4 mt-6 leading-[1.45] text-text"
            >
              {beat.narration}
            </p>
            {question ? (
              <p data-testid="beat-question" className="font-display text-5 mt-4 text-primary">
                {question}
              </p>
            ) : null}

            {audioUrl ? (
              <button
                type="button"
                // The whole scene is a tap target too, so the click is stopped
                // here rather than starting the narration twice.
                onClick={(event) => {
                  event.stopPropagation();
                  speak({ force: true });
                }}
                aria-label={
                  heard
                    ? `Hear ${companionName} tell it again`
                    : `Hear ${companionName} tell the story`
                }
                data-testid="beat-replay"
                className={`mt-6 min-h-target min-w-target rounded-full px-8 text-6 font-display shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95 ${
                  heard ? 'bg-surface text-primary' : 'bg-accent text-surface'
                }`}
              >
                ▶
              </button>
            ) : null}

            {/* Day 5 turns this into real choice buttons. Today the beat ends
                `awaiting_choice` and the child is simply invited to answer out
                loud — which is what the companion just asked them to do. */}
            {awaitingChoice && beat.interaction && beat.interaction.type !== 'none' ? (
              <p data-testid="beat-awaiting" className="font-display text-3 mt-6 text-text-muted">
                Tell {companionName} what you think.
              </p>
            ) : null}
          </>
        ) : (
          /* Not a loading message — a line of story. There is no state on this
             screen in which the child is told the system is busy. */
          <p data-testid="story-thinking" className="font-display text-4 mt-6 text-text-muted">
            {companionName} is thinking of somewhere to take you…
          </p>
        )}
      </div>

      {audioUrl ? (
        <audio
          ref={audioRef}
          src={audioUrl}
          data-testid="beat-audio"
          preload="auto"
          // `onPlay` rather than the `play()` promise: the element is the thing
          // that knows, and it covers a play started some other way too.
          onPlay={() => setHeard(true)}
          onEnded={() => setHeard(true)}
        />
      ) : null}

      <footer className="mt-8 text-1 text-text-muted">
        <Link to="/" className="underline underline-offset-4">
          Back
        </Link>
      </footer>

      <style>{`
        @keyframes breathe {
          0%, 100% { transform: scale(1) translateY(0); }
          50%      { transform: scale(1.02) translateY(-6px); }
        }
        @keyframes think {
          0%, 100% { transform: translateY(0); opacity: 0.4; }
          50%      { transform: translateY(-8px); opacity: 1; }
        }
        @keyframes fade-in {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
      `}</style>
    </main>
  );
}

/**
 * The companion, thinking. This is what stands where a spinner would be.
 *
 * It uses the *real* portrait when the reference set exists, because the point is
 * that the child is looking at their friend and not at a widget. Without one it
 * falls back to a soft shape — still a character, still breathing. Either way the
 * three dots are the only thing that indicates activity, and they read as
 * thinking rather than as loading.
 */
function ThinkingCompanion({ portraitUrl, name }: { portraitUrl: string | null; name: string }) {
  return (
    <div className="flex flex-col items-center" data-testid="companion-thinking">
      {portraitUrl ? (
        <img
          src={portraitUrl}
          alt={`${name} is thinking`}
          className="w-[min(46vw,15rem)] rounded-lg motion-safe:animate-[breathe_4s_ease-in-out_infinite]"
        />
      ) : (
        <svg
          viewBox="0 0 200 160"
          className="w-[min(46vw,15rem)]"
          role="img"
          aria-label={`${name} is thinking`}
        >
          <ellipse cx="100" cy="142" rx="66" ry="11" fill="var(--border)" opacity="0.6" />
          <g className="motion-safe:animate-[breathe_4s_ease-in-out_infinite]">
            <ellipse cx="100" cy="98" rx="60" ry="44" fill="var(--primary)" opacity="0.85" />
            <circle cx="82" cy="90" r="5" fill="var(--surface)" />
            <circle cx="118" cy="90" r="5" fill="var(--surface)" />
          </g>
        </svg>
      )}

      <div aria-hidden="true" className="mt-4 flex gap-2">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="inline-block h-3 w-3 rounded-full bg-accent motion-safe:animate-[think_1.4s_ease-in-out_infinite]"
            style={{ animationDelay: `${index * 0.18}s` }}
          />
        ))}
      </div>
    </div>
  );
}
