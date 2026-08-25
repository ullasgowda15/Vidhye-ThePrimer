/**
 * Recording a child reading a line aloud.
 *
 * A thin wrapper over `MediaRecorder`, and almost all of it is the stopping
 * rules — because the stopping rules are what a five-year-old actually
 * experiences.
 *
 * **The silence threshold is the thing Vidhye got wrong and wrote down.** Its
 * `listen()` ended a turn after 1.5 seconds of quiet, tuned on an adult reading
 * fluently, and its own plan file predicts the consequence in as many words: *"a
 * child sounding out sh-i-p pauses three or four seconds mid-word. Expect this
 * to fire constantly and make the whole thing feel broken."* A cut-off mid-word
 * is not a truncated recording, it is a child being interrupted by the friend
 * who asked them to read. So the quiet window here is deliberately long, and the
 * child can always finish early by tapping.
 *
 * There is no voice-activity detection and no noise gate. Both are ways of
 * being cleverer about when a child has stopped talking, and both fail on
 * exactly the child this is for — the hesitant one. A generous timer and a
 * visible "done" control are duller and right.
 *
 * **Re-derived from the engine's own `listen()`** (`Vidhye/frontend/lib/speech.ts`),
 * because the port had quietly changed three things that had been tuned in front
 * of children, and each of them ends a turn early:
 *
 *   * the silence countdown ran from the moment the microphone opened, instead of
 *     from the first sound the child made — see `heardSpeech`, which is the whole
 *     ball game;
 *   * `SPEECH_LEVEL` was four times more sensitive than the tuned value, so room
 *     tone counted as talking;
 *   * `getByteTimeDomainData` was handed a half-length buffer.
 *
 * The tuned numbers are cited where they are defined. Change them against real
 * recordings, not against an adult testing it at a desk — that is the mistake
 * the engine's own notes were written to stop us repeating.
 */

/** How long a stretch of quiet ends the turn — but only *after* the child has
 *  started, which is the rule that matters. See `heardSpeech` below. */
const SILENCE_MS = 4_000;

/** The ceiling, for a recorder nobody stopped. Matches the engine's own 45s
 *  rather than the 25s this was ported at: the ceiling is not a pacing control,
 *  it is the backstop against a pocketed tablet, and set tight it becomes a
 *  second way to truncate a slow reader. The child's tap is the fast path. */
const MAX_MS = 45_000;

/** Below this there is no reading in the clip — a mis-tap, or a child who
 *  changed their mind. Reported as "did not speak" rather than sent. */
const MIN_MS = 600;

/**
 * How loud counts as not-silence, as a fraction of full scale.
 *
 * `8 / 128` — the engine's proven raw threshold, converted. The port picked
 * `0.015`, which is raw ~1.9 and four times more sensitive than anything that
 * has been in front of a child: at that level a quiet room's own noise floor
 * reads as speech, so the turn never goes quiet and every reading runs to the
 * ceiling instead of ending when the child does.
 */
const SPEECH_LEVEL = 8 / 128;

/**
 * Full scale for the level meter, as a fraction.
 *
 * `40 / 128`, also the engine's. A child reading to a tablet peaks nowhere near
 * the digital maximum, so a meter normalised against 1.0 barely moves and looks
 * like it is not hearing anything. This is the number that makes the dot
 * respond the way it does in the tutor.
 */
const FULL_SCALE = 40 / 128;

export interface Recording {
  /** Null when nothing usable was captured — see `MIN_MS`. */
  audio: Blob | null;
  durationMs: number;
}

export interface ActiveRecording {
  /** Resolves when the recorder stops, whichever rule stopped it. */
  done: Promise<Recording>;
  /** Finish now. What the child's "done" control calls. Safe to call twice. */
  stop: () => void;
  /** Current input level, 0..1, for drawing something that responds to a voice. */
  level: () => number;
}

/** Is recording possible at all here? Older browsers and insecure origins say no.
 *
 * **The insecure-origin half is the one that bites in development.** `navigator
 * .mediaDevices` is simply `undefined` outside a secure context, and a phone on
 * the LAN reaches the dev server over plain `http://10.x.x.x:5173` — only
 * `localhost` gets the free pass. So this returns `false` on the exact device a
 * reading moment most needs testing on, while production behind Caddy's TLS is
 * fine. That is a property of the origin, not a broken browser, and it is why
 * `ReadingMoment` has to stay usable when this says no rather than disabling
 * itself. */
export function canRecord(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.MediaRecorder !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia)
  );
}

/**
 * Ask for the microphone and start recording.
 *
 * Rejects if permission is refused or the device has no microphone — the caller
 * treats that as "the child did not speak", which is true and is not their
 * fault. Nothing about a refused microphone should reach the child as an error.
 */
export async function record(): Promise<ActiveRecording> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      // A child reads close to a tablet in a room with other people in it.
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const recorder = new MediaRecorder(stream);
  const chunks: Blob[] = [];
  const startedAt = Date.now();

  // Level metering, and the silence timer that reads it. A separate graph from
  // the recorder so stopping one never affects the other.
  //
  // `webkitAudioContext` is still the only constructor on older iOS.
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  const context = new Ctor();

  // **Resumed, because this is reached from inside a `catch`-able await chain.**
  // Safari starts an `AudioContext` suspended unless it is constructed during a
  // user gesture, and the `await getUserMedia()` above has already ended the
  // gesture this was called from. A suspended analyser reports a flat 128 — pure
  // silence — for as long as it stays suspended, which the meter below would read
  // as "nobody is talking" and act on. See `meterLive`.
  await context.resume().catch(() => undefined);

  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  // `fftSize`, not `frequencyBinCount`. `getByteTimeDomainData` fills a buffer of
  // `fftSize` samples; handed the half-length bin count it writes only the first
  // half of each window, so the meter was judging "is anybody talking" from half
  // the audio it was looking at.
  const samples = new Uint8Array(analyser.fftSize);

  let current = 0;
  let stopped = false;

  /**
   * Has the child actually started yet?
   *
   * **This is the rule the port dropped, and the one that made it feel broken.**
   * The engine counts quiet only once it has heard speech — its comment is
   * "children pause for a long time before beginning" — whereas this started the
   * countdown the instant the microphone opened. A five-year-old looking at a
   * sentence, working out that it is their turn, gets four seconds; the recorder
   * then stopped and thanked them for reading, before they had said a word.
   *
   * It also makes the whole meter fail safe. If the `AudioContext` could not be
   * resumed, or the device hands back flat silence, `heardSpeech` simply stays
   * false and nothing here can end a child's turn — the ceiling and the "All
   * done" button do, and neither depends on a measurement being right.
   */
  let heardSpeech = false;
  let quietSince: number | null = null;

  const meter = window.setInterval(() => {
    analyser.getByteTimeDomainData(samples);
    // Peak deviation from the 128 midpoint, as a fraction of full scale. Cheaper
    // than RMS and sufficient for "is anybody talking".
    let peak = 0;
    for (const sample of samples) {
      const deviation = Math.abs(sample - 128) / 128;
      if (deviation > peak) peak = deviation;
    }
    current = Math.min(1, peak / FULL_SCALE);

    const now = Date.now();
    if (now - startedAt > MAX_MS) {
      stop();
      return;
    }

    if (peak > SPEECH_LEVEL) {
      heardSpeech = true;
      quietSince = null;
      return;
    }
    // Still waiting for them to begin. No amount of this ends the turn.
    if (!heardSpeech) return;

    if (quietSince === null) quietSince = now;
    else if (now - quietSince > SILENCE_MS && now - startedAt > MIN_MS) stop();
  }, 100);

  function cleanup() {
    window.clearInterval(meter);
    for (const track of stream.getTracks()) track.stop();
    void context.close().catch(() => undefined);
  }

  function stop() {
    if (stopped) return;
    stopped = true;
    try {
      if (recorder.state !== 'inactive') recorder.stop();
    } catch {
      // Already stopping. The promise below still settles from `onstop`.
    }
  }

  const done = new Promise<Recording>((resolve) => {
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = () => {
      cleanup();
      const durationMs = Date.now() - startedAt;
      const audio = chunks.length > 0 ? new Blob(chunks, { type: recorder.mimeType }) : null;
      resolve({
        audio: durationMs >= MIN_MS ? audio : null,
        durationMs,
      });
    };
    recorder.onerror = () => {
      cleanup();
      resolve({ audio: null, durationMs: Date.now() - startedAt });
    };
  });

  recorder.start();

  return { done, stop, level: () => current };
}
