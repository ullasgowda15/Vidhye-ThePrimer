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
 */

/** How long a stretch of quiet ends the turn. See the note above; this is long
 *  on purpose, and it is the single number most worth re-tuning once real
 *  children have used it. */
const SILENCE_MS = 4_000;

/** The ceiling, for a recorder nobody stopped. A K-1 sentence is a few seconds;
 *  this is loose enough to never truncate a real attempt and tight enough that a
 *  pocketed tablet does not upload a minute of a kitchen. */
const MAX_MS = 25_000;

/** Below this there is no reading in the clip — a mis-tap, or a child who
 *  changed their mind. Reported as "did not speak" rather than sent. */
const MIN_MS = 600;

/** How loud counts as not-silence. Deliberately low: a child reading to
 *  themselves is much quieter than an adult performing, and the cost of being
 *  too sensitive is a longer recording, while the cost of being too strict is
 *  the interruption this module exists to avoid. */
const SPEECH_LEVEL = 0.015;

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

/** Is recording possible at all here? Older browsers and insecure origins say no. */
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
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const samples = new Uint8Array(analyser.frequencyBinCount);

  let current = 0;
  let lastHeardAt = Date.now();
  let stopped = false;

  const meter = window.setInterval(() => {
    analyser.getByteTimeDomainData(samples);
    // Peak deviation from the 128 midpoint, normalised. Cheaper than RMS and
    // sufficient for "is anybody talking".
    let peak = 0;
    for (const sample of samples) {
      const deviation = Math.abs(sample - 128) / 128;
      if (deviation > peak) peak = deviation;
    }
    current = peak;
    const now = Date.now();
    if (peak > SPEECH_LEVEL) lastHeardAt = now;

    const elapsed = now - startedAt;
    const quietFor = now - lastHeardAt;
    if (elapsed > MAX_MS || (elapsed > MIN_MS && quietFor > SILENCE_MS)) stop();
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
