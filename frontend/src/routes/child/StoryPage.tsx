/**
 * The story (PRODUCT.md §4.9, §8.5, §14.5, §18.5).
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
 *   2. the `media` frames → the illustration crossfades in; the audio starts; each
 *      choice card's picture drops into the card that is already on screen
 *   3. `done` → the child is invited to act
 *
 * The narration is never held back for the picture. That ordering is the whole
 * reason the backend streams instead of returning a finished beat.
 *
 * **The loop.** One stream open delivers one beat. The child acts, the choice goes
 * back over `POST …/choice`, and the stream is reopened for the consequence
 * (§18.6). Two things make that seamless rather than a page transition: the
 * previous beat *stays on screen* until the next one arrives — blanking it would
 * be a spinner wearing different clothes — and the server has usually already
 * generated the consequence by the time we reconnect, because submitting the
 * choice started it (§4.9).
 *
 * **Nothing here can be got wrong.** There is no failure state in this file, for
 * the network or for the child. A count the child misses does not produce an
 * error, a red mark, or a blocked path: the companion offers to do it together,
 * `assisted` goes back with the answer, and the story continues (§8.5).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { api } from '@/lib/api/client';
import { useAdventure, useCompanion, useResetAdventure } from '@/lib/api/queries';
import { consumeBeatStream, type BeatEvent, type ChoiceOption } from '@/lib/sse';

import ChoiceCards from './ChoiceCard';

const collapse = (text: string) => text.split(/\s+/).join(' ').toLowerCase();

/** Where the beat has got to. There is no `error` member, by design (§13). */
type Status = 'thinking' | 'awaiting_choice' | 'complete';

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

/**
 * A short, soft blip acknowledging a tap.
 *
 * §14.7 wants every child action acknowledged within ~100ms, in motion *and*
 * sound, so the world feels responsive while the next beat generates. Synthesised
 * rather than a downloaded asset because it must be instant and must work before
 * any media has loaded. Every failure is swallowed: no audio context, a browser
 * that refuses one before a gesture, a test environment without the API — none of
 * them are worth anything on a child's screen.
 */
function playTapChime() {
  try {
    const Ctor =
      window.AudioContext ??
      (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const context = new Ctor();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(660, context.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(990, context.currentTime + 0.12);
    gain.gain.setValueAtTime(0.14, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.22);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.24);
    oscillator.onended = () => void context.close().catch(() => undefined);
  } catch {
    // Silence is an acceptable outcome; a thrown error on a child's tap is not.
  }
}

export default function StoryPage() {
  const { childId } = useParams();
  const navigate = useNavigate();
  const { data: companion } = useCompanion(childId);
  // Resumes the story in progress before it would ever start a new one, so a
  // reload lands the child back in the same forest (§14.5).
  const { data: adventure } = useAdventure(childId);
  const resetAdventure = useResetAdventure(childId);

  const [beat, setBeat] = useState<BeatEvent | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  /** Choice icons, which land after the cards they belong to. */
  const [choiceImages, setChoiceImages] = useState<Record<string, string>>({});
  /** Has the narration begun playing? Not "finished" — see `handleSceneTap`. */
  const [heard, setHeard] = useState(false);
  const [status, setStatus] = useState<Status>('thinking');
  /** The option just tapped, so the card can respond before the network does. */
  const [chosen, setChosen] = useState<string | null>(null);
  /** The companion's offer to help, once the child has had a go (§8.5). */
  const [scaffold, setScaffold] = useState<string | null>(null);
  /**
   * Bumped to reopen the stream for the next beat. The URL does not change
   * between beats — one open delivers one beat — so something has to say "again".
   */
  const [turn, setTurn] = useState(0);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  /** When this beat became answerable, for `latencyMs`. */
  const readyAt = useRef<number | null>(null);
  /** Attempts on this beat, and whether the companion ended up helping (§10.4). */
  const attempts = useRef(0);
  const assisted = useRef(false);
  /**
   * Has this beat already been answered?
   *
   * A ref rather than the `chosen` state, and the difference is the whole point: a
   * child double-tapping a big forgiving target produces two clicks inside one
   * render, and a state read would still be `null` for the second one. The server
   * settles a duplicate too, but a second request that advances nothing is a
   * second request we should not have sent.
   */
  const answered = useRef(false);

  const streamUrl = adventure?.streamUrl;
  const adventureId = adventure?.adventureId;
  const companionName = companion?.name ?? adventure?.companion.name ?? 'Your friend';

  useEffect(() => {
    if (!streamUrl) return;
    const controller = new AbortController();

    void consumeBeatStream(streamUrl, {
      signal: controller.signal,
      onBeat: (event) => {
        // The new beat replaces the old one here, and not a moment earlier: the
        // previous scene stays up across the reconnect so there is no flash of the
        // thinking companion between beats (§4.9).
        setBeat(event);
        setImageUrl(event.scene.imageUrl);
        setAudioUrl(event.audioUrl);
        setChoiceImages(
          Object.fromEntries(
            (event.choices ?? [])
              .filter((option) => option.imageUrl)
              .map((option) => [option.key, option.imageUrl as string]),
          ),
        );
        setHeard(false);
        setChosen(null);
        setScaffold(null);
        setStatus('thinking');
        attempts.current = 0;
        assisted.current = false;
        answered.current = false;
      },
      onMedia: (event) => {
        if (event.scene?.imageUrl) setImageUrl(event.scene.imageUrl);
        if (event.audioUrl) setAudioUrl(event.audioUrl);
        if (event.choice) {
          const { key, imageUrl: url } = event.choice;
          setChoiceImages((current) => ({ ...current, [key]: url }));
        }
      },
      onEnd: (done) => {
        if (done?.status === 'complete') setStatus('complete');
        else if (done?.status === 'awaiting_choice') setStatus('awaiting_choice');
        // `ended` and a dropped connection leave the status where it was: the
        // child keeps the scene they are looking at and is never told why (§13).
        readyAt.current = Date.now();
      },
    });

    return () => controller.abort();
    // Keyed on the stream URL and the turn. The token is read inside the client at
    // the moment the connection opens — and refreshed there if it has expired — so
    // it is not a dependency here: re-opening the stream because a token rotated
    // mid-beat would restart the narration.
  }, [streamUrl, turn]);

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
  const speak = useCallback(({ force = false }: { force?: boolean } = {}) => {
    const element = audioRef.current;
    if (!element || !element.src) return;
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
  }, []);

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
  }, [audioUrl, heard, speak]);

  /**
   * Send what the child did and move on to the consequence.
   *
   * The tap has already been acknowledged on screen by the time this runs — the
   * card is lifted and the chime has played — so the several seconds the next beat
   * takes are spent looking at the scene they just changed rather than at a
   * loading state. A failed POST is deliberately silent and simply reopens the
   * stream: the server replays the beat the child is still on, and they get
   * another go at it (§13).
   */
  const submit = useCallback(
    async (choiceKey: string, value?: number | string) => {
      if (answered.current) return;
      answered.current = true;
      setChosen(choiceKey);
      playTapChime();

      if (adventureId) {
        try {
          await api.submitChoice(adventureId, {
            beatId: beat?.beatId ?? '',
            choiceKey,
            value: value ?? null,
            latencyMs: readyAt.current ? Date.now() - readyAt.current : null,
            retries: attempts.current,
            assisted: assisted.current,
          });
        } catch {
          // Never surfaced. Reopening below re-reads whatever the server thinks
          // is true, which is the only recovery a child could ever perform.
        }
      }
      setTurn((count) => count + 1);
    },
    [adventureId, beat?.beatId],
  );

  /**
   * A count the child did not get. **Not** a wrong answer (§8.5).
   *
   * The first miss buys them a hand rather than a mark: the companion offers to
   * count together, `assisted` is set for the beat's evidence, and the next tap is
   * accepted whatever it is. Nothing is disabled, nothing turns red, and the word
   * "wrong" appears nowhere — a child who has already had one go is not made to
   * keep guessing until they land on a number.
   */
  function handleCount(value: number) {
    const expected = beat?.interaction?.expected;
    const wanted = typeof expected === 'number' ? expected : null;

    if (wanted !== null && value !== wanted && attempts.current === 0) {
      attempts.current += 1;
      assisted.current = true;
      playTapChime();
      setScaffold(`Let's count them together — I'll help you.`);
      return;
    }
    void submit('count', value);
  }

  /** The child has reached the ending and told us which way they want to go. */
  async function finish(playAgain: boolean) {
    playTapChime();
    if (adventureId) {
      try {
        await api.endAdventure(adventureId, 'completed');
      } catch {
        // Same posture as `submit`: the child is not the person who can fix this.
      }
    }
    if (playAgain) {
      // The old adventure is finished, so asking again starts a new one.
      resetAdventure();
      setBeat(null);
      setImageUrl(null);
      setAudioUrl(null);
      setStatus('thinking');
      setTurn((count) => count + 1);
      return;
    }
    // Home is `/` — the child tree has no per-child landing route (`router.tsx`),
    // and the one place a child ends up is where they started.
    void navigate('/');
  }

  const question = askedSeparately(beat);
  const options: ChoiceOption[] = (beat?.choices ?? []).map((option) => ({
    ...option,
    imageUrl: choiceImages[option.key] ?? option.imageUrl,
  }));
  const counting =
    beat?.interaction?.type === 'count' && typeof beat.interaction.expected === 'number';
  /**
   * Is the child's turn on screen?
   *
   * Deliberately *not* also `chosen === null`. The card the child tapped has to
   * stay up and show that it was tapped — lifted, its siblings faded — because
   * that is the acknowledgement §14.7 asks for within ~100ms, and a row of cards
   * that vanishes on touch is a screen that swallowed the tap. The controls are
   * replaced when the next beat arrives, not when the tap lands; `answered` is
   * what stops a second one being sent.
   */
  const answerable = status === 'awaiting_choice';

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

            {/* The companion offering a hand. Never an error, never a correction —
                it appears only *with* another go, never instead of one (§8.5). */}
            {scaffold ? (
              <p data-testid="beat-scaffold" className="font-display text-4 mt-4 text-success">
                {scaffold}
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

            {status === 'complete' ? (
              <WarmClose companionName={companionName} onFinish={finish} />
            ) : null}

            {answerable && options.length > 0 ? (
              <ChoiceCards
                options={options}
                chosen={chosen}
                onChoose={(option) => void submit(option.key)}
              />
            ) : null}

            {answerable && options.length === 0 && counting ? (
              <CountPad expected={beat.interaction?.expected as number} onCount={handleCount} />
            ) : null}

            {answerable && options.length === 0 && !counting ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  void submit('continue');
                }}
                data-testid="beat-continue"
                aria-label="Carry on"
                className="mt-8 min-h-target rounded-full bg-accent px-10 text-5 font-display text-surface shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95"
              >
                Let&rsquo;s go →
              </button>
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

      {/* §14.5: exit is always one obvious tap, because a parent may need to stop
          instantly. Navigation only — it leaves the adventure `in_progress`, so
          coming back lands in the same forest rather than starting a new story. */}
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
 * Numbers to tap for a counting beat.
 *
 * Digits rather than pictures, because a numeral *is* the thing being practised
 * here and the objects to count are already in the illustration. Still large,
 * spaced, forgiving targets (§14.4), and still no wrong one: `handleCount` decides
 * what a miss means, and what it means is help.
 *
 * The range always reaches past the answer, so the right number is never simply
 * the largest one on screen.
 */
function CountPad({ expected, onCount }: { expected: number; onCount: (value: number) => void }) {
  const highest = Math.max(5, expected + 2);
  const numbers = Array.from({ length: highest }, (_, index) => index + 1);

  return (
    <div
      role="group"
      aria-label="How many?"
      data-testid="count-pad"
      className="mt-8 flex flex-wrap items-center justify-center gap-4"
    >
      {numbers.map((value) => (
        <button
          key={value}
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onCount(value);
          }}
          data-testid={`count-${value}`}
          aria-label={`${value}`}
          className="min-h-target min-w-target rounded-full bg-surface text-6 font-display text-primary shadow-card transition-transform duration-base motion-safe:hover:scale-110 active:scale-95"
        >
          {value}
        </button>
      ))}
    </div>
  );
}

/**
 * The ending (§8.8, §14.3).
 *
 * Two big, illustrated, voiced choices and nothing else. No score, no stars
 * earned, no time played, no streak — none of which a four-year-old can act on and
 * all of which turn a story into a performance (§14.5). The companion has already
 * said goodbye and planted tomorrow's hook in the narration above; this is only
 * the way out.
 */
function WarmClose({
  companionName,
  onFinish,
}: {
  companionName: string;
  onFinish: (playAgain: boolean) => void;
}) {
  return (
    <div
      role="group"
      aria-label="What next?"
      data-testid="warm-close"
      className="mt-8 flex flex-wrap items-center justify-center gap-5"
    >
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onFinish(true);
        }}
        data-testid="close-again"
        aria-label={`Go somewhere new with ${companionName}`}
        className="min-h-target rounded-full bg-accent px-10 text-5 font-display text-surface shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95"
      >
        Again!
      </button>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onFinish(false);
        }}
        data-testid="close-done"
        aria-label="All done for today"
        className="min-h-target rounded-full bg-surface px-10 text-5 font-display text-primary shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95"
      >
        All done
      </button>
    </div>
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
