/**
 * The stopping rules (`lib/recorder.ts`).
 *
 * These exist because the rules regressed once already, silently, in the port
 * from the engine — and a recorder that ends a child's turn too early cannot be
 * caught by any test of the screen above it. Every assertion here is about
 * *when* the turn ends, which is the only thing about this module a five-year-old
 * experiences.
 *
 * The whole browser side is faked: a `MediaRecorder` that reports its state, and
 * an analyser whose loudness the test drives directly. What is under test is the
 * arithmetic between them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { record } from '@/lib/recorder';

/** Raw byte deviation from the 128 midpoint. The module's threshold is 8. */
let loudness = 0;

class FakeRecorder {
  state = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;

  start() {
    this.state = 'recording';
  }

  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(['x'], { type: 'audio/webm' }) });
    this.onstop?.();
  }
}

let recorder: FakeRecorder;

beforeEach(() => {
  vi.useFakeTimers();
  loudness = 0;
  recorder = new FakeRecorder();

  const track = { stop: vi.fn() };
  vi.stubGlobal(
    'MediaRecorder',
    vi.fn(() => recorder),
  );
  vi.stubGlobal('AudioContext', function AudioContextStub() {
    return {
      state: 'running',
      resume: () => Promise.resolve(),
      close: () => Promise.resolve(),
      createMediaStreamSource: () => ({ connect: () => undefined }),
      createAnalyser: () => ({
        fftSize: 1024,
        frequencyBinCount: 512,
        connect: () => undefined,
        getByteTimeDomainData: (buffer: Uint8Array) => buffer.fill(128 + loudness),
      }),
    };
  });
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: () => Promise.resolve({ getTracks: () => [track] }) },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Let the meter run for `ms`, with the microphone at the current `loudness`. */
async function elapse(ms: number) {
  await vi.advanceTimersByTimeAsync(ms);
}

describe('when a reading turn ends', () => {
  it('waits through a long silence before the child has said anything', async () => {
    // The regression this file exists for. The engine's `listen()` starts its
    // silence countdown at the child's first sound; the port started it when the
    // microphone opened, so a child still working out that it is their turn was
    // stopped and thanked after four seconds of looking at the sentence.
    const session = await record();
    let ended = false;
    void session.done.then(() => {
      ended = true;
    });

    loudness = 0; // an empty room, and a child thinking
    await elapse(20_000);

    expect(ended).toBe(false);
    expect(recorder.state).toBe('recording');
  });

  it('ends the turn once the child has spoken and then gone quiet', async () => {
    const session = await record();
    let ended = false;
    void session.done.then(() => {
      ended = true;
    });

    loudness = 30; // reading, comfortably over the threshold of 8
    await elapse(3_000);
    expect(ended).toBe(false);

    loudness = 0; // finished
    await elapse(2_000);
    // Not yet: four seconds of quiet, because a child sounding out "sh-i-p"
    // pauses for three.
    expect(ended).toBe(false);

    await elapse(3_000);
    expect(ended).toBe(true);
  });

  it('does not count a quiet room as talking', async () => {
    // `SPEECH_LEVEL` was ported four times more sensitive than the tuned value,
    // at which a room's own noise floor keeps the turn alive forever.
    const session = await record();
    let ended = false;
    void session.done.then(() => {
      ended = true;
    });

    loudness = 30;
    await elapse(1_000);
    loudness = 3; // hum, breathing, the fridge — under the threshold of 8
    await elapse(6_000);

    expect(ended).toBe(true);
  });

  it('stops at the ceiling even if nothing is ever heard', async () => {
    // The backstop for a pocketed tablet, and the only rule left when the meter
    // is dead — a suspended `AudioContext` reports flat silence forever.
    const session = await record();
    let ended = false;
    void session.done.then(() => {
      ended = true;
    });

    loudness = 0;
    await elapse(46_000);

    expect(ended).toBe(true);
  });

  it('lets the child finish early by tapping', async () => {
    const session = await record();
    const done = session.done;

    loudness = 30;
    await elapse(800);
    session.stop();

    const { audio, durationMs } = await done;
    expect(audio).not.toBeNull();
    expect(durationMs).toBeGreaterThanOrEqual(600);
  });

  it('reports a mis-tap as nothing said rather than as a clip', async () => {
    const session = await record();
    const done = session.done;

    await elapse(100); // under `MIN_MS`
    session.stop();

    expect((await done).audio).toBeNull();
  });
});
