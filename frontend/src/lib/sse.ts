/**
 * The beat stream client (PRODUCT.md §18.5).
 *
 * **Why not `EventSource`.** It cannot set an `Authorization` header, and both
 * ways around that are worse than writing the parser: a token in the query string
 * lands in every access log and proxy cache, and a session cookie on a GET
 * reintroduces exactly the CSRF surface the httpOnly refresh cookie was scoped to
 * avoid. So this reads the response body with `fetch` + a bearer token. What we
 * give up is `EventSource`'s automatic reconnect, which we would have had to
 * replace anyway — a story cannot silently restart from beat one.
 *
 * **Why a hand-rolled parser.** The wire format is four lines and a blank line.
 * The only part with a real trap is that a chunk boundary can fall anywhere,
 * including inside a JSON payload, so frames are split on the blank line and the
 * remainder is carried over — never parsed per chunk.
 *
 * **Failure is silence.** Every error path calls `onEnd` and nothing else. This
 * runs on the child surface, where there is no error state: the caller keeps
 * showing the companion thinking (§13, §14.5). Errors are reported to the console
 * for a developer, never to the screen.
 *
 * **The connection is opened by `client.ts`, not here.** This used to take an
 * access token and call `fetch` itself, which made it the one authenticated call
 * in the app with no silent refresh — an access token that expired in the seconds
 * before the stream opened turned into a 401, and a 401 on the child surface is a
 * companion who thinks forever. `openStream` gives it the same
 * refresh-then-retry-once path every other authenticated call has.
 */

import { openStream } from './api/client';

/** The `beat` frame (§18.5). */
export interface BeatEvent {
  beatId: string;
  seq: number;
  narration: string;
  scene: { imageUrl: string | null; status: 'pending' | 'ready' };
  audioUrl: string | null;
  interaction: {
    type: 'count' | 'choose' | 'say' | 'tap' | 'none';
    prompt_audio: string | null;
    expected: number | string | null;
    choices: string[] | null;
  } | null;
  emotion: string;
}

/**
 * The `media` frame — one asset that has finished rendering.
 *
 * A separate event from `beat` on purpose: from Day 5 the stream carries several
 * different beats, and a client that had to tell "a new beat" from "an update to
 * a beat" by comparing ids is a client that will get it wrong.
 */
export interface MediaEvent {
  beatId: string;
  scene?: { imageUrl: string; status: 'ready' };
  audioUrl?: string;
}

/** The `done` frame. `awaiting_choice` is the only non-degraded status today. */
export interface DoneEvent {
  status: 'awaiting_choice' | 'ended';
}

export interface StreamHandlers {
  onBeat?: (beat: BeatEvent) => void;
  onMedia?: (media: MediaEvent) => void;
  /** Called exactly once, however the stream finished — including on failure. */
  onEnd?: (done: DoneEvent | null) => void;
}

interface Frame {
  event: string;
  data: string;
}

const FRAME_SEPARATOR = /\r?\n\r?\n/;

/**
 * Split a buffer into complete frames plus whatever is left over.
 *
 * Exported for its own test: this is the one piece of logic here that a chunk
 * boundary in an unlucky place can break, and the failure mode — a dropped beat —
 * looks like a backend problem from the outside.
 */
export function parseFrames(buffer: string): { frames: Frame[]; rest: string } {
  const parts = buffer.split(FRAME_SEPARATOR);
  // The last piece is either an incomplete frame or an empty string after a
  // clean boundary. Either way it is not ours to parse yet.
  const rest = parts.pop() ?? '';
  const frames: Frame[] = [];

  for (const block of parts) {
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      // A line starting with ':' is a comment — the server sends one to flush
      // headers before the several seconds of generation that follow.
      if (line.startsWith(':')) continue;
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trim());
    }
    if (data.length > 0) frames.push({ event, data: data.join('\n') });
  }

  return { frames, rest };
}

/**
 * Consume a beat stream until it ends.
 *
 * Resolves when the stream is over. Abort the passed signal to stop early — the
 * child navigating away is the normal case, not an error.
 */
export async function consumeBeatStream(
  url: string,
  { signal, ...handlers }: StreamHandlers & { signal?: AbortSignal },
): Promise<void> {
  let ended = false;
  const end = (done: DoneEvent | null) => {
    if (ended) return;
    ended = true;
    handlers.onEnd?.(done);
  };

  try {
    const response = await openStream(url, signal);

    if (!response.ok || !response.body) {
      // A 401, a 403, a 502 — all the same to a four-year-old. The caller keeps
      // the idle scene on screen; the console line is for whoever is debugging.
      console.warn(`[sse] ${url} did not open (${response.status})`);
      end(null);
      return;
    }

    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += value;
      const { frames, rest } = parseFrames(buffer);
      buffer = rest;

      for (const frame of frames) {
        dispatch(frame, handlers, end);
        if (ended) {
          // `done` arrived. Release the connection rather than waiting for the
          // server to close it.
          await reader.cancel().catch(() => undefined);
          return;
        }
      }
    }
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) {
      console.warn('[sse] stream failed', error);
    }
  } finally {
    end(null);
  }
}

function dispatch(frame: Frame, handlers: StreamHandlers, end: (done: DoneEvent | null) => void) {
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    // A frame we cannot read is a frame we skip. Tearing the story down over one
    // malformed payload would turn a server-side glitch into a blank screen.
    console.warn('[sse] unparseable frame', frame);
    return;
  }

  switch (frame.event) {
    case 'beat':
      handlers.onBeat?.(payload as BeatEvent);
      break;
    case 'media':
      handlers.onMedia?.(payload as MediaEvent);
      break;
    case 'done':
      end(payload as DoneEvent);
      break;
    default:
      // Forward compatibility: Day 5's `prefetch` event (§18.5) must not break a
      // client that predates it.
      break;
  }
}
