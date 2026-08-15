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
  const active = useRef<ActiveRecording | null>(null);

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
    try {
      const session = await record();
      active.current = session;
      const { audio, durationMs } = await session.done;
      active.current = null;
      await send(audio, durationMs);
    } catch {
      // A refused microphone, or a browser that will not give one up. Recorded
      // server-side as a turn where the child did not speak, which is exactly
      // what happened from our side.
      await send(null, 0);
    }
  }, [send]);

  if (!invitation?.offered || !invitation.text) return null;

  return (
    <section
      data-testid="reading-moment"
      aria-label={`${companionName} would like you to read`}
      className="mt-8 flex w-full max-w-xl flex-col items-center gap-5 rounded-lg bg-surface/60 p-6 shadow-card"
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
          <p
            data-testid="reading-line"
            lang="en"
            className="text-center font-display text-6 leading-relaxed tracking-wide text-text"
          >
            {invitation.text}
          </p>

          {phase === 'offered' ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                void listen();
              }}
              disabled={!canRecord()}
              data-testid="reading-start"
              aria-label="Read it to me"
              className="min-h-target rounded-full bg-accent px-10 text-5 font-display text-surface shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95 disabled:opacity-40"
            >
              I&rsquo;ll read it
            </button>
          ) : null}

          {phase === 'listening' ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                active.current?.stop();
              }}
              data-testid="reading-done"
              aria-label="I have finished reading"
              className="min-h-target rounded-full bg-primary px-10 text-5 font-display text-surface shadow-card transition-transform duration-base active:scale-95"
            >
              {/* A visible way to finish, because the silence timer is deliberately
                  long — a child sounding a word out must never be cut off, so the
                  fluent one needs a way not to wait. */}
              <span className="mr-3 inline-block h-3 w-3 rounded-full bg-surface motion-safe:animate-[breathe_1.5s_ease-in-out_infinite]" />
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
