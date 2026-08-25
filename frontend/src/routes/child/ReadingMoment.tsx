/**
 * "Will you read this one to me?" (Docs/Build/Reading-Integration.md §8.)
 *
 * Sits on the warm close, after the story has resolved. That position is the
 * design: the adventure is finished, so nothing is interrupted; the child is
 * still with their companion rather than on a screen about reading; and the
 * "Again!" and "All done" buttons stay on screen throughout, so declining is
 * done by ignoring this rather than by refusing it.
 *
 * **Nothing here judges the reading**, and the component has no way to. The
 * server returns a warm line and no verdict, because in Phase 1 nothing has
 * listened to the audio — the detector's every threshold was tuned against an
 * adult imitating a child, and a mark produced from that would be a guess shown
 * to a five-year-old as a fact. What this screen is for is the invitation and,
 * where a parent has consented, the recording that lets Phase 2 fix the guessing.
 *
 * Every failure resolves to the same place: the companion says something kind
 * and the child moves on. A refused microphone, a browser with no recorder, a
 * dead network — none of them are the child's fault and none of them appear
 * (§13).
 *
 * **That includes having no microphone at all.** This used to disable the button
 * when `canRecord()` said no, which put a dead grey control under a goodbye and
 * left a child tapping something that could never answer — the one shape §13
 * exists to forbid, and the failure most likely to be seen, because an insecure
 * origin turns `canRecord()` off on every phone reaching a dev server over the
 * LAN. The invitation is the point of Phase 1 and it does not need a recorder:
 * without one the child still reads the line aloud to their companion and still
 * gets thanked, and the turn goes up as `spoke: false`, which is exactly what
 * happened from our side.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type ReadingInvitation } from '@/lib/api/client';
import { canRecord, record, type ActiveRecording } from '@/lib/recorder';

type Phase = 'offered' | 'listening' | 'sending' | 'answered';

export function ReadingMoment({
  childId,
  sessionId,
  companionName,
}: {
  childId: string;
  sessionId?: string | null;
  companionName: string;
}) {
  const [invitation, setInvitation] = useState<ReadingInvitation | null>(null);
  const [phase, setPhase] = useState<Phase>('offered');
  const [reply, setReply] = useState<string | null>(null);
  /** How loud it is right now, 0..1, for the dot. See the effect below. */
  const [level, setLevel] = useState(0);
  const active = useRef<ActiveRecording | null>(null);
  /** Tapped "All done" before the microphone prompt was answered. */
  const finishedEarly = useRef(false);
  const startedAt = useRef(0);

  // Read once, not per render: this cannot change while the screen is up, and a
  // control that appears or vanishes underneath a child is worse than either.
  const [recordable] = useState(canRecord);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    api
      .readingInvitation(childId, controller.signal)
      .then((result) => {
        if (!cancelled) setInvitation(result);
      })
      .catch(() => {
        // Not offered, then. A reading invitation that could not be fetched is a
        // screen with one fewer thing on it, which is a complete and acceptable
        // state — and far better than an error under a goodbye.
        if (!cancelled) setInvitation({ offered: false });
      });

    return () => {
      cancelled = true;
      controller.abort();
      active.current?.stop();
    };
  }, [childId]);

  const send = useCallback(
    async (audio: Blob | null, durationMs: number) => {
      if (!invitation?.passageId) return;
      setPhase('sending');
      try {
        const result = await api.submitReading(childId, {
          passageId: invitation.passageId,
          audio,
          durationMs,
          sessionId: sessionId ?? null,
        });
        setReply(result.say);
      } catch {
        // The recording is lost and the child must not learn that. They read to
        // their friend; their friend says thank you.
        setReply('Thank you for reading to me.');
      }
      setPhase('answered');
    },
    [childId, invitation?.passageId, sessionId],
  );

  const listen = useCallback(async () => {
    setPhase('listening');
    startedAt.current = Date.now();
    finishedEarly.current = false;

    // No recorder here — an insecure origin, or a browser too old for one. The
    // child reads to their companion exactly as they would otherwise; what is
    // missing is only our ability to keep the audio, which was never the part
    // they could see. `finish` sends the turn when they tap.
    if (!recordable) return;

    try {
      const session = await record();
      active.current = session;
      // The permission sheet can sit on screen for a while, and a child who
      // finished reading behind it has already said they are done.
      if (finishedEarly.current) session.stop();
      const { audio, durationMs } = await session.done;
      active.current = null;
      await send(audio, durationMs);
    } catch {
      // A refused microphone, or a browser that will not give one up. Recorded
      // server-side as a turn where the child did not speak, which is exactly
      // what happened from our side. The elapsed time still goes up: "invited,
      // stayed eight seconds" and "invited, tapped straight past" are different
      // findings, and `spoke` is decided by the audio rather than by this.
      await send(null, Date.now() - startedAt.current);
    }
  }, [recordable, send]);

  /** The child's "All done" — and the only way to finish when there is no mic. */
  const finish = useCallback(() => {
    const session = active.current;
    if (session) {
      session.stop();
      return;
    }
    // Either the recorder has not come back yet, or there was never going to be
    // one. Both end the turn; only the first has a recording to wait for.
    finishedEarly.current = true;
    if (!recordable) void send(null, Date.now() - startedAt.current);
  }, [recordable, send]);

  /**
   * Make the dot answer the child's voice.
   *
   * `recorder.level()` has existed since Phase 1 and nothing has ever read it,
   * so the "listening" dot has been breathing on a CSS timer that runs the same
   * whether the microphone is hearing a shout or is muted. A child looking for a
   * sign that they are being listened to has had a light that pulses at nothing,
   * and the person testing it has no way to tell a working microphone from a
   * dead one. Driving it from the actual level makes both legible.
   */
  useEffect(() => {
    if (phase !== 'listening' || !recordable) return;
    let frame = 0;
    const tick = () => {
      setLevel(active.current?.level() ?? 0);
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [phase, recordable]);

  if (!invitation?.offered || !invitation.text) return null;

  return (
    <section
      data-testid="reading-moment"
      aria-label={`${companionName} would like you to read`}
      className="mt-8 flex w-full max-w-xl flex-col items-center gap-5 rounded-lg bg-surface/60 p-4 shadow-card sm:p-6"
    >
      {phase === 'answered' ? (
        <p data-testid="reading-reply" className="font-display text-4 text-primary">
          {reply}
        </p>
      ) : (
        <>
          <p className="font-display text-4 text-text-muted">{invitation.prompt}</p>

          {/* The line itself, set large and loose. This is the one piece of text
              in the child tree that is meant to be *read* rather than heard, so
              it gets the size and spacing that asks for. */}
          {/* `text-5` until there is room for `text-6`. The bank's longest line —
              "Then they think of the thin cloth." — is 34 characters, and at 40px
              with `tracking-wide` that wraps to three ragged lines in the ~264px a
              360px phone leaves inside this card. It is the sentence the child is
              being asked to *read*, so how it breaks is the feature. */}
          <p
            data-testid="reading-line"
            lang="en"
            className="max-w-full break-words text-center font-display text-5 leading-relaxed tracking-wide text-text sm:text-6"
          >
            {invitation.text}
          </p>

          {/* `px-10` at `text-5` overflowed a 360px phone on its own. The pair
              below stay inside it: horizontal padding that grows only when there
              is room, and a width ceiling so a long label wraps rather than
              widening the card. Both keep `min-h-target` — 64px on this surface,
              which is the floor a small finger needs (§15.4). */}
          {phase === 'offered' ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                void listen();
              }}
              data-testid="reading-start"
              aria-label="Read it to me"
              className="min-h-target max-w-full rounded-full bg-accent px-6 py-3 text-5 font-display text-surface shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95 sm:px-10"
            >
              I&rsquo;ll read it
            </button>
          ) : null}

          {phase === 'listening' ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                finish();
              }}
              data-testid="reading-done"
              aria-label="I have finished reading"
              className="flex min-h-target max-w-full items-center justify-center rounded-full bg-primary px-6 py-3 text-5 font-display text-surface shadow-card transition-transform duration-base active:scale-95 sm:px-10"
            >
              {/* A visible way to finish, because the silence timer is deliberately
                  long — a child sounding a word out must never be cut off, so the
                  fluent one needs a way not to wait.

                  The dot grows with the child's own voice (see the effect above),
                  so it is a sign of being heard rather than decoration. With no
                  recorder there is no level to show and it falls back to the
                  breathing animation, which is honest: nothing is listening. */}
              <span
                aria-hidden="true"
                data-testid="reading-level"
                style={
                  recordable ? { transform: `scale(${1 + Math.min(level, 1) * 1.6})` } : undefined
                }
                className={`mr-3 inline-block h-3 w-3 shrink-0 rounded-full bg-surface ${
                  recordable
                    ? 'transition-transform duration-fast'
                    : 'motion-safe:animate-[breathe_1.5s_ease-in-out_infinite]'
                }`}
              />
              All done
            </button>
          ) : null}

          {phase === 'sending' ? (
            <p data-testid="reading-sending" className="font-display text-4 text-text-muted">
              {companionName} is listening&hellip;
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
