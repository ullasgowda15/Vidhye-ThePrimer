/**
 * Coming back — the day-two payoff (PRODUCT.md §4.5, §14.5, §14.7).
 *
 * §4.5 calls this "the whole business", and the reason is not the feature: it is
 * that a child who is *remembered* has a friend, and a child who is greeted
 * warmly by something that remembers nothing has an app. So the entire budget of
 * this screen is spent on one moment — the companion arrives, says the child's
 * name, and says the thing they did last time out loud.
 *
 * It is deliberately the same scene as the imprint (`MeetCompanion.tsx`) one visit
 * later, and the shared vocabulary is the point: the companion breathes, the sun
 * replays the greeting, the star opens the story. A four-year-old learns those two
 * glyphs once and they keep working. What is different is the *arrival* — the
 * companion comes in from the side rather than simply being there — because being
 * met is a different feeling from being found waiting.
 *
 * Three rules hold, all of them the same rule (§13):
 *
 * - **No spinner.** While the greeting is being written, the companion is walking
 *   in. Waiting is narrated, not indicated.
 * - **No error.** A failed request, a provider outage, no memories at all — every
 *   one of those resolves to a warm hello and a star to tap. The backend falls
 *   back to the first-meeting greeting for anything it can see; anything it
 *   cannot, this scene absorbs by rendering the companion and nothing else.
 * - **No reading required.** The greeting is spoken. The caption is for the adult
 *   in the room and for anyone using a screen reader.
 *
 * `kind` decides the heading and nothing else. "Welcome back" is a claim about
 * having been here before, and a scene that guessed at it from the text would
 * eventually say it to a child on their first day — which is the one thing this
 * screen must never do.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { useCompanion, useGreeting } from '@/lib/api/queries';

export default function ReturningGreeting() {
  const { childId } = useParams();
  const navigate = useNavigate();
  const { data: companion } = useCompanion(childId);
  const { data: arrival, isPending } = useGreeting(childId);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  /** Has the greeting begun playing? Not "finished" — see `handleSceneTap`. */
  const [heard, setHeard] = useState(false);

  const audioUrl = arrival?.audio_url ?? null;
  const name = companion?.name ?? arrival?.companion_name ?? '';
  const returning = arrival?.kind === 'returning';

  /**
   * Start the greeting, swallowing every way that can fail.
   *
   * Identical in shape to `MeetCompanion.speak`, and deliberately not shared: the
   * two scenes will diverge (this one is a fresh recording each visit, that one is
   * cached forever), and a premature abstraction over "play a sound" would couple
   * the imprint moment to changes made for this one.
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

  /** The fallback for a browser that refused autoplay, and only that. */
  function handleSceneTap() {
    if (heard) return;
    speak();
  }

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
        {companion?.portrait_url ? (
          <img
            src={companion.portrait_url}
            alt={`${name}, your companion`}
            data-testid="companion-portrait"
            // Two motions layered: the arrival plays once, the breath never
            // stops. `motion-safe:` on both, so prefers-reduced-motion leaves a
            // still companion who is simply there (§14.6).
            className="w-[min(70vw,26rem)] rounded-lg shadow-card motion-safe:animate-[arrive_900ms_cubic-bezier(0.22,1,0.36,1)_both,breathe_4s_ease-in-out_infinite_900ms]"
          />
        ) : (
          <ArrivingFriend />
        )}
      </div>

      {isPending ? (
        /* Not a loading state — a sentence about what is happening, which is that
           somebody is on their way over. Only while the request is genuinely in
           flight: a request that has *failed* is not somebody still coming, and
           leaving this on screen would strand the child watching for a friend who
           is never going to arrive. */
        <p
          className="font-display text-4 mt-7 max-w-[30rem] text-text-muted"
          data-testid="greeting-arriving"
        >
          Someone is coming to say hello…
        </p>
      ) : (
        <>
          {/* Everything below `isPending` renders with or without a greeting, and
              that is the point. The backend already falls back to the first-meeting
              hello for every failure it can see, so reaching here with no `arrival`
              means the *request* failed — and the child must still be able to get
              to their story. They lose the hello, not the evening (§13, §4.9). */}
          <h1 className="font-display text-6 mt-6 text-primary" data-testid="greeting-heading">
            {returning ? `Welcome back!` : name}
          </h1>

          {/* The caption, not the message. The line the child hears is the audio. */}
          {arrival?.text ? (
            <p
              className="font-display text-4 mt-3 max-w-[34rem] text-text"
              data-testid="greeting-text"
            >
              {arrival.text}
            </p>
          ) : null}

          {/* What the companion is remembering, shown as well as said. A greeting
              that claims to remember something is a claim the adult in the room is
              entitled to check (§12.6) — and seeing it written is how a parent
              first realises the product is doing the thing it promised. */}
          {returning && arrival?.remembered ? (
            <p
              className="font-text text-2 mt-4 max-w-[30rem] text-text-muted italic"
              data-testid="greeting-remembered"
            >
              {arrival.remembered}
            </p>
          ) : null}

          {/* Two glyphs, no words — the same two as the imprint scene, meaning the
              same two things. Whichever action is next carries the accent. */}
          <div className="mt-7 flex items-center justify-center gap-5">
            {audioUrl ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  speak({ force: true });
                }}
                aria-label={heard ? `Hear ${name} again` : `Hear what ${name} has to say`}
                data-testid="greeting-replay"
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
              aria-label={`Begin today's story with ${name}`}
              data-testid="begin-story"
              className={`min-h-target min-w-target rounded-full px-8 text-6 font-display shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95 ${
                heard || !audioUrl ? 'bg-accent text-surface' : 'bg-surface text-primary'
              }`}
            >
              ★
            </button>
          </div>
        </>
      )}

      {audioUrl ? (
        <audio
          ref={audioRef}
          src={audioUrl}
          data-testid="greeting-audio"
          preload="auto"
          onPlay={() => setHeard(true)}
          onEnded={() => setHeard(true)}
        />
      ) : null}

      <style>{`
        @keyframes arrive {
          from { transform: translateX(-14%) scale(0.94); opacity: 0; }
          to   { transform: translateX(0) scale(1); opacity: 1; }
        }
        @keyframes breathe {
          0%, 100% { transform: scale(1) translateY(0); }
          50%      { transform: scale(1.02) translateY(-6px); }
        }
        @keyframes stroll {
          0%, 100% { transform: translateX(-6px); }
          50%      { transform: translateX(6px); }
        }
      `}</style>
    </main>
  );
}

/**
 * The waiting state: the companion walking over, not a loading indicator.
 *
 * The distinction is the same one `MeetCompanion.SleepingFriend` draws. A child
 * looking at this sees a friend on their way, which is a thing that happens to
 * friends — and if the portrait never arrives, this is a scene rather than a gap.
 */
function ArrivingFriend() {
  return (
    <svg
      viewBox="0 0 240 200"
      className="w-[min(60vw,20rem)]"
      role="img"
      aria-label="Your companion is on their way over"
    >
      <ellipse cx="120" cy="176" rx="86" ry="14" fill="var(--border)" opacity="0.6" />
      <g className="motion-safe:animate-[stroll_3s_ease-in-out_infinite]">
        <g className="motion-safe:animate-[breathe_4s_ease-in-out_infinite]">
          <ellipse cx="120" cy="122" rx="70" ry="56" fill="var(--primary)" opacity="0.85" />
          <ellipse cx="120" cy="134" rx="44" ry="32" fill="var(--surface)" opacity="0.7" />
          {/* Open eyes, unlike the sleeping shape: this friend is awake and
              looking for you, which is the whole difference between the scenes. */}
          <circle cx="102" cy="112" r="6" fill="var(--text)" />
          <circle cx="138" cy="112" r="6" fill="var(--text)" />
          <path
            d="M104 136 q 16 14 32 0"
            stroke="var(--text)"
            strokeWidth="4"
            fill="none"
            strokeLinecap="round"
          />
        </g>
      </g>
    </svg>
  );
}
