/**
 * The parent gate (PRODUCT.md §14.2).
 *
 * A child who taps their way out of the story must not land in billing or in
 * their own safety settings. The gate is a small multiplication — the standard,
 * COPPA-aligned "an adult is holding this" check. It is not security: it is a
 * door a three-year-old cannot open by accident, and it is honest about that.
 *
 * The passed flag is short-lived and in memory only, so leaving the tablet on
 * the sofa does not leave the parent area open (see `PARENT_GATE_TTL_MS`).
 */

import { useCallback, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useAuthStore } from '@/store/auth';

import { Field, Notice, ParentShell, PrimaryButton, TextInput } from './ui';

interface Challenge {
  a: number;
  b: number;
}

function newChallenge(previous?: Challenge): Challenge {
  // Two digits times one digit: trivial for an adult, out of reach for a
  // pre-reader who cannot yet multiply.
  //
  // Re-rolled until it differs from the one just failed. There are only 54
  // sums, so a random re-roll repeats itself about one time in fifty — and a
  // gate that re-offers the sum you just got wrong looks broken.
  let next: Challenge;
  do {
    next = {
      a: 11 + Math.floor(Math.random() * 9),
      b: 3 + Math.floor(Math.random() * 6),
    };
  } while (previous && next.a === previous.a && next.b === previous.b);
  return next;
}

export default function ParentGate() {
  const navigate = useNavigate();
  const location = useLocation();
  const passParentGate = useAuthStore((state) => state.passParentGate);

  const [challenge, setChallenge] = useState<Challenge>(() => newChallenge());
  const [answer, setAnswer] = useState('');
  const [failed, setFailed] = useState(false);

  const destination = (location.state as { from?: string } | null)?.from ?? '/parent';

  const submit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      if (Number(answer) === challenge.a * challenge.b) {
        passParentGate();
        navigate(destination, { replace: true });
        return;
      }
      // A new sum each time, so a child cannot get in by mashing the last answer.
      setFailed(true);
      setAnswer('');
      setChallenge(newChallenge(challenge));
    },
    [answer, challenge, destination, navigate, passParentGate],
  );

  return (
    <ParentShell
      title="Grown-ups only"
      subtitle="This part of The Primer is for parents. Answer to continue."
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label={`What is ${challenge.a} × ${challenge.b}?`}>
          <TextInput
            name="answer"
            inputMode="numeric"
            autoComplete="off"
            // Overrides the visible label for screen readers only: "×" is read
            // as "multiplication sign" or skipped entirely by some readers.
            aria-label={`What is ${challenge.a} times ${challenge.b}?`}
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
          />
        </Field>

        <PrimaryButton type="submit">Continue</PrimaryButton>

        {failed ? <Notice>Not quite — here&apos;s a new one.</Notice> : null}
      </form>
    </ParentShell>
  );
}
