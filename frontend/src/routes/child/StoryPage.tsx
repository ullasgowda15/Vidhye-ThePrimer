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

import SceneTransition from '@/components/SceneTransition';
import { safeVolume, useAccessibility, withDefaults, wordsOnScreen } from '@/design/accessibility';
import { isNightTime, useWorldTheme, worldNames, isWorld } from '@/design/worlds';
import { api } from '@/lib/api/client';
import {
  useAdventure,
  useChildSettings,
  useCompanion,
  useResetAdventure,
  useWellbeing,
} from '@/lib/api/queries';
import { playOrReportBlocked } from '@/lib/audioUnlock';
import { waitingLine } from '@/lib/motion';
import { consumeBeatStream, type BeatEvent, type ChoiceOption } from '@/lib/sse';

import { TapToHear } from '@/components/TapToHear';

import ChoiceCards from './ChoiceCard';
import { ReadingMoment } from './ReadingMoment';

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
  /**
   * The parent's controls (§12.7), read here for the accessibility half of them.
   *
   * The safety half is enforced server-side and this screen never sees it — which
   * is the correct split: a client that had to be trusted to apply an avoid-topic
   * would be a client that could be made not to.
   */
  const { data: settings } = useChildSettings(childId);
  const access = withDefaults(settings?.accessibility);
  useAccessibility(settings?.accessibility);

  /**
   * §13.6's daily window, asked *before* a story is requested.
   *
   * `isPending` matters as much as `open` does: until the answer is back we hold
   * the adventure request, so a child arriving after bedtime never briefly starts
   * a story that is then taken away. The wait is covered by the same thinking
   * companion that covers everything else on this screen.
   */
  const { data: gate, isPending: gatePending } = useWellbeing(childId);
  const resting = gate?.open === false;

  // Resumes the story in progress before it would ever start a new one, so a
  // reload lands the child back in the same forest (§14.5).
  const { data: adventure } = useAdventure(childId, !gatePending && !resting);
  const resetAdventure = useResetAdventure(childId);

  const [beat, setBeat] = useState<BeatEvent | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  /** Choice icons, which land after the cards they belong to. */
  const [choiceImages, setChoiceImages] = useState<Record<string, string>>({});
  /** Has the narration begun playing? Not "finished" — see `handleSceneTap`. */
  const [heard, setHeard] = useState(false);
  /** The browser refused to start the narration. See `lib/audioUnlock`. */
  const [blocked, setBlocked] = useState(false);
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
  /**
   * Has the child acted, with the consequence not yet on screen?
   *
   * This is the gap §4.9 exists to cover, and it is deliberately *not* derived
   * from `status`: it opens the instant the tap lands, before the POST is even
   * sent, because the whole point is that the motion starts with the child's
   * finger rather than with the network. It closes when the next `beat` frame
   * replaces the scene.
   */
  const [awaitingNext, setAwaitingNext] = useState(false);

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

  /**
   * The world this adventure is set in, worn as a palette (§15.1, §8.3).
   *
   * Read off the adventure rather than off the child, because the server decided
   * where this story goes when it planned it and a screen that chose for itself
   * could disagree with the words being narrated.
   *
   * Night is read from the clock rather than from a stored preference, because
   * §15.1's low-stimulation mode is about the room the child is in and not about a
   * setting anybody remembered to change. It is a plain boolean, so it only ever
   * repaints when the hour actually crosses.
   */
  const worldKey = adventure?.world?.key;
  const night = isNightTime();
  useWorldTheme(worldKey, night);

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
        // The consequence is here; the path-walk that covered its generation
        // stops, and `SceneTransition` turns the page over to it.
        setAwaitingNext(false);
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
    // The rejection is the interesting outcome, and it used to be discarded:
    // both "speaking" and "the browser said no" arrived here as `undefined`, so
    // the screen could not tell the child which had happened. `blocked` is what
    // puts the cue on the glass (`TapToHear`).
    void playOrReportBlocked(element).then((playing) => setBlocked(!playing));
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
      // Before the request, not after it: §14.7 wants the world to respond within
      // ~100ms, and a round trip is not within 100ms.
      setAwaitingNext(true);

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

  // §14.6, §7.13. The three-way rule lives in `design/accessibility` because the
  // server states the same rule in `ChildSettings.words_on_screen`, and a rule
  // written in two places has to be testable in both.
  const voiceOn = settings?.voice_on ?? true;
  /**
   * The second argument is the Day-15 half of rule 3 (`wordsOnScreen`).
   *
   * A beat can reach this screen with no `audioUrl` for reasons nobody chose — a
   * voice vendor out of quota with no fallback configured, the inference-budget
   * breaker declining a new line (§18.8). Without telling the rule that, a
   * parent who had turned the story text off would get a beat that is neither
   * heard nor read, which is not a beat at all.
   *
   * The `status` check is what stops that becoming a flicker. Audio arrives in a
   * later `media` frame than the narration, so "no audio yet" is the normal state
   * for the first second of every beat; only once `done` has landed is the voice
   * genuinely not coming. Until then the words are treated as spoken-for, and a
   * parent who chose no text does not see a paragraph appear and vanish.
   */
  const showWords = wordsOnScreen(settings, status === 'thinking' || audioUrl !== null);

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

  // §13.6. A scene, not a refusal: the companion is resting and says so in their
  // own voice, and there is nothing on this screen to argue with, nothing
  // counting down, and nothing inviting the child to come back at a time. The
  // line comes from the server so "the window is shut" and "how a companion says
  // it" cannot drift apart (`story.wellbeing.Gate`).
  if (resting) {
    return (
      <main
        data-testid="wellbeing-resting"
        className="min-h-full bg-bg text-text flex flex-col items-center justify-center p-6 text-center"
      >
        <div className="w-full max-w-[36rem]">
          <ThinkingCompanion
            portraitUrl={companion?.portrait_url ?? null}
            name={companionName}
            resting
          />
          <p className="font-display text-4 mt-6 leading-[1.45] text-text">{gate?.message}</p>
        </div>
        <footer className="mt-8 text-1 text-text-muted">
          <Link to="/" className="underline underline-offset-4">
            Back
          </Link>
        </footer>
      </main>
    );
  }

  return (
    <main
      onClick={handleSceneTap}
      // Where they are, for the grown-up in the room and for a screen reader. The
      // child is *told* where they are by the companion (§14.6) — nothing on this
      // surface is read, so this label is the only place the world is named in text.
      aria-label={isWorld(worldKey) ? `A story in ${worldNames[worldKey]}` : undefined}
      data-world={isWorld(worldKey) ? worldKey : undefined}
      className="min-h-full bg-bg text-text flex flex-col items-center justify-center p-6 text-center"
    >
      <div className="w-full max-w-[44rem]">
        {/* Every gap in the whole screen is inside this: the wait before beat one,
            the wait between beats, and the wait for an illustration that is slow
            or is never coming. None of them is a spinner (§4.9). */}
        <SceneTransition
          sceneKey={beat?.beatId ?? 'thinking'}
          pending={awaitingNext}
          caption={awaitingNext ? waitingLine(companionName, beat?.seq ?? 0) : null}
        >
          {/* The picture's slot is reserved from the start so the narration does
              not jump down the page when the illustration lands. */}
          <div className="relative mx-auto flex min-h-[14rem] w-full items-center justify-center">
            {imageUrl ? (
              <img
                src={imageUrl}
                alt={beat?.scene.status === 'ready' ? 'The scene' : ''}
                data-testid="beat-illustration"
                className="w-full rounded-lg shadow-card motion-safe:animate-[fade-in_var(--motion-slow)_var(--motion-ease)]"
              />
            ) : (
              /* Not "no picture yet" — the companion, thinking. This is also what
                 stands in when the image provider is down entirely, and the beat
                 below it is fully playable regardless (§4.9). */
              <ThinkingCompanion
                portraitUrl={companion?.portrait_url ?? null}
                name={companionName}
              />
            )}
          </div>
        </SceneTransition>

        {beat ? (
          <>
            {/* The caption, not the message: the line the child *hears* is the
                audio. It is on screen for the adult in the room and for
                accessibility (§14.6), and `wordsOnScreen` is the parent's control
                over that — with the rule that the accessibility flag and a silent
                voice both outrank the preference. */}
            {showWords ? (
              <p
                data-testid="beat-narration"
                className="font-display text-4 mt-6 leading-[1.45] text-text"
              >
                {beat.narration}
              </p>
            ) : null}
            {showWords && question ? (
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

            {audioUrl && voiceOn ? (
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
              <>
                {/* After the story has resolved, and above the buttons rather
                    than instead of them: declining is done by ignoring this and
                    tapping "Again!" or "All done", which is the only kind of
                    refusal a five-year-old should have to perform. Renders
                    nothing at all unless the server offers a line — most
                    children are pre-readers and get no invitation. */}
                {childId ? <ReadingMoment companionName={companionName} childId={childId} /> : null}
                <WarmClose companionName={companionName} onFinish={finish} />
              </>
            ) : null}

            {/* Only when a `play()` was actually refused, and only while the
                narration has not been heard. The container's tap handler is the
                target; this just makes the invitation visible (§14.5 — the
                child cannot read, so it cannot be a sentence). */}
            {blocked && !heard ? <TapToHear companionName={companionName} /> : null}

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

      {/* `voice_on` off means the element is not rendered at all, rather than
          rendered muted: a muted element still downloads the clip, and a parent who
          switched the voice off has asked us not to fetch it. `wordsOnScreen` is
          forced true in that case, so the beat is still fully delivered (§14.6). */}
      {audioUrl && voiceOn ? (
        <audio
          src={audioUrl}
          data-testid="beat-audio"
          preload="auto"
          // §7.13's adjustable audio. Set through a ref callback because `volume`
          // is a DOM *property*, not an attribute — React will not apply it from
          // JSX, so writing `volume={…}` here would be a control that silently did
          // nothing, which is the exact failure §12.7 forbids.
          ref={(element) => {
            audioRef.current = element;
            if (element) element.volume = safeVolume(access.audio_volume);
          }}
          // `onPlay` rather than the `play()` promise: the element is the thing
          // that knows, and it covers a play started some other way too.
          // `onPlay` clears the cue as well as arming `heard`: the element is
          // the thing that knows it is speaking, and it covers a play started
          // some other way too.
          onPlay={() => {
            setHeard(true);
            setBlocked(false);
          }}
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
function ThinkingCompanion({
  portraitUrl,
  name,
  resting = false,
}: {
  portraitUrl: string | null;
  name: string;
  /**
   * The same character, having a rest rather than thinking (§13.6).
   *
   * Deliberately this component and not a new one: the child must see *their
   * friend* when the day's window has closed, exactly as they do while a beat
   * generates. What changes is the label a screen reader reads and the three
   * dots, which mean "something is coming" and would be a small untruth here.
   */
  resting?: boolean;
}) {
  const label = resting ? `${name} is having a rest` : `${name} is thinking`;

  return (
    <div className="flex flex-col items-center" data-testid="companion-thinking">
      {portraitUrl ? (
        <img
          src={portraitUrl}
          alt={label}
          className="w-[min(46vw,15rem)] rounded-lg motion-safe:animate-[breathe_4s_ease-in-out_infinite]"
        />
      ) : (
        <svg viewBox="0 0 200 160" className="w-[min(46vw,15rem)]" role="img" aria-label={label}>
          <ellipse cx="100" cy="142" rx="66" ry="11" fill="var(--border)" opacity="0.6" />
          <g className="motion-safe:animate-[breathe_4s_ease-in-out_infinite]">
            <ellipse cx="100" cy="98" rx="60" ry="44" fill="var(--primary)" opacity="0.85" />
            {/* Closed eyes when resting — two lines rather than two dots. */}
            {resting ? (
              <>
                <path d="M76 90 q6 5 12 0" stroke="var(--surface)" strokeWidth="3" fill="none" />
                <path d="M112 90 q6 5 12 0" stroke="var(--surface)" strokeWidth="3" fill="none" />
              </>
            ) : (
              <>
                <circle cx="82" cy="90" r="5" fill="var(--surface)" />
                <circle cx="118" cy="90" r="5" fill="var(--surface)" />
              </>
            )}
          </g>
        </svg>
      )}

      {resting ? null : (
        <div aria-hidden="true" className="mt-4 flex gap-2">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="inline-block h-3 w-3 rounded-full bg-accent motion-safe:animate-[think_1.4s_ease-in-out_infinite]"
              style={{ animationDelay: `${index * 0.18}s` }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
