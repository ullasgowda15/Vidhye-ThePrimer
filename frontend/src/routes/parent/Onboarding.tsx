/**
 * Onboarding (PRODUCT.md §4.2): account → consent → child → companion → handover.
 *
 * The order is the product's argument in miniature. Consent comes before any
 * child data is typed, because the gate is server-enforced anyway (§13.7) and
 * because asking afterwards would be asking a parent to approve something they
 * have already done. Everything about the child except a name and a birthdate
 * is skippable: a tired parent should be able to finish in a minute and still
 * get a good first story from defaults plus age.
 *
 * It ends by handing the tablet over rather than at a dashboard, because the
 * thing this flow exists to produce is not an account — it is a child meeting
 * their companion (§4.3).
 *
 * **The step indicator is drawn as well as counted** (§14.6, §4.2). "Step 2 of 4"
 * is a true sentence and a poor one: a parent two minutes into setting up a
 * product for their child wants to know how much is left, and a number does not
 * answer that at a glance. The dots do, and the sentence stays underneath them
 * for anyone the dots do not serve — colour is never the sole signal, and the
 * whole thing is one `aria-label` to a screen reader rather than four unlabelled
 * shapes.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { errorMessage, type Child } from '@/lib/api/client';
import { useCreateChild, useRecordConsent } from '@/lib/api/queries';
import { useAuthStore } from '@/store/auth';

import AccountForm from './AccountForm';
import CompanionPicker from './CompanionPicker';
import { Field, Notice, ParentShell, PrimaryButton, QuietButton, TextInput } from './ui';

type Step = 'account' | 'consent' | 'child' | 'companion' | 'handover';

/** Turn "dinosaurs, the sea" into ["dinosaurs", "the sea"]. */
function toSeeds(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 10);
}

const INTENSITY_LABELS = [
  'Gentlest — wonder and warmth only',
  'Mild — small problems, solved quickly',
  'Middle — real stakes, always resolved',
  'Adventurous — bigger obstacles, never scary',
];

const STEPS: Step[] = ['account', 'consent', 'child', 'companion', 'handover'];

export default function Onboarding() {
  const navigate = useNavigate();
  const authenticated = useAuthStore((state) => state.accessToken !== null);

  const [step, setStep] = useState<Step>(authenticated ? 'consent' : 'account');
  /** The child the companion is being created for. Set by the child step. */
  const [child, setChild] = useState<{ id: string; name: string } | null>(null);

  return (
    <ParentShell
      title="Set up The Primer"
      subtitle={step === 'handover' ? undefined : 'A couple of minutes, and mostly optional.'}
    >
      {step === 'handover' ? null : <Steps current={step} />}
      {step === 'account' ? <AccountForm onDone={() => setStep('consent')} /> : null}
      {step === 'consent' ? <ConsentStep onDone={() => setStep('child')} /> : null}
      {step === 'child' ? (
        <ChildStep
          onDone={(created) => {
            // Skipping the child step means there is nobody for a companion to
            // belong to, so onboarding ends at the dashboard instead.
            if (!created) return navigate('/parent', { replace: true });
            setChild({ id: created.id, name: created.first_name });
            setStep('companion');
          }}
        />
      ) : null}
      {step === 'companion' && child ? (
        <CompanionPicker
          childId={child.id}
          childName={child.name}
          onDone={() => setStep('handover')}
        />
      ) : null}
      {step === 'handover' && child ? <HandoverStep childId={child.id} name={child.name} /> : null}
    </ParentShell>
  );
}

/** The four setup steps, in order, with the words a parent would use for them. */
const STEP_LABELS: Record<Exclude<Step, 'handover'>, string> = {
  account: 'Your account',
  consent: 'Permission',
  child: 'About your child',
  companion: 'Their friend',
};

function Steps({ current }: { current: Step }) {
  const index = STEPS.indexOf(current);
  const total = STEPS.length - 1;
  const label = STEP_LABELS[current as Exclude<Step, 'handover'>];

  return (
    <div className="mb-6">
      {/* One label for the whole indicator. Four separately-announced dots would
          be four pieces of noise; this is one sentence, and it is the same
          sentence the sighted reader gets below. */}
      <ol className="flex gap-2" aria-label={`Step ${index + 1} of ${total}: ${label}`}>
        {STEPS.slice(0, total).map((step, position) => (
          <li
            key={step}
            aria-hidden="true"
            className={`h-1.5 flex-1 rounded-full transition-colors duration-base ${
              position <= index ? 'bg-primary' : 'bg-border'
            }`}
          />
        ))}
      </ol>
      <p className="mt-2 text-1 text-text-muted">
        Step {index + 1} of {total} · {label}
      </p>
    </div>
  );
}

/**
 * The moment the tablet changes hands (§4.3).
 *
 * A separate screen on purpose: the imprint works because the child arrives at
 * a companion that is already waiting for them, not because they watched a
 * parent fill in a form and then got handed the result.
 */
function HandoverStep({ childId, name }: { childId: string; name: string }) {
  const navigate = useNavigate();

  return (
    <div className="space-y-4">
      <h2 className="text-4 font-semibold">Someone is waiting to meet {name}</h2>
      <p className="text-2 text-text-muted">
        Their friend is getting ready — it takes a moment to wake up. Hand {name} the tablet and let
        them say hello. You can come back to this dashboard any time.
      </p>
      <PrimaryButton type="button" onClick={() => navigate(`/meet/${childId}`)}>
        Hand over the tablet
      </PrimaryButton>
      <QuietButton type="button" onClick={() => navigate('/parent', { replace: true })}>
        Not now — go to my dashboard
      </QuietButton>
    </div>
  );
}

function ConsentStep({ onDone }: { onDone: () => void }) {
  const recordConsent = useRecordConsent();
  const [attested, setAttested] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await recordConsent.mutateAsync({
        consent_type: 'data_collection_v1',
        method: 'checkbox_attestation',
      });
      onDone();
    } catch (caught) {
      setError(errorMessage(caught, 'We could not record your consent.'));
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <h2 className="text-4 font-semibold">Before we start</h2>

      {/* Plain language, no legalese wall. What we keep, what we never do, and
          the two promises that matter most to a parent (§12.8). */}
      <div className="space-y-3 text-2">
        <p>
          The Primer keeps a small profile of your child so their stories can remember them: a first
          name, an age, and the things you tell us they love, fear, or should never meet in a story.
        </p>
        <p>
          We never sell this, never use it to train anyone else&apos;s models, and never show your
          child advertising. You can read everything we hold, and delete all of it in one action, at
          any time.
        </p>
        <p>
          Stories are generated by AI and checked before your child sees them. You set the intensity
          ceiling and the topics to avoid, and those are honoured immediately.
        </p>
      </div>

      <label className="flex items-start gap-3 text-2">
        <input
          type="checkbox"
          className="mt-1"
          checked={attested}
          onChange={(event) => setAttested(event.target.checked)}
        />
        <span>
          I am this child&apos;s parent or legal guardian, and I consent to The Primer collecting
          and using their information as described above.
        </span>
      </label>

      <PrimaryButton type="submit" disabled={!attested || recordConsent.isPending}>
        {recordConsent.isPending ? 'Saving…' : 'I agree — continue'}
      </PrimaryButton>

      <Notice>{error}</Notice>
    </form>
  );
}

function ChildStep({ onDone }: { onDone: (child: Child | null) => void }) {
  const createChild = useCreateChild();

  const [firstName, setFirstName] = useState('');
  const [birthdate, setBirthdate] = useState('');
  const [interests, setInterests] = useState('');
  const [fears, setFears] = useState('');
  const [avoid, setAvoid] = useState('');
  const [intensity, setIntensity] = useState(2);
  const [sessionCap, setSessionCap] = useState(15);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await createChild.mutateAsync({
        first_name: firstName,
        birthdate,
        interests: toSeeds(interests),
        fears: toSeeds(fears),
        avoid_topics: toSeeds(avoid),
        // The slider is stored as the 0-3 int the safety layer reads — never as
        // the label the parent saw.
        intensity_ceiling: intensity,
        session_cap_min: sessionCap,
      });
      onDone(created);
    } catch (caught) {
      setError(errorMessage(caught, 'We could not save that profile.'));
    }
  }

  const name = firstName.trim() || 'them';

  return (
    <form onSubmit={submit} className="space-y-4">
      <h2 className="text-4 font-semibold">Tell us about your child</h2>

      <Field label="First name" hint="Or the nickname the companion should use.">
        <TextInput
          name="first_name"
          required
          value={firstName}
          onChange={(event) => setFirstName(event.target.value)}
        />
      </Field>

      <Field label="Birthday">
        <TextInput
          name="birthdate"
          type="date"
          required
          value={birthdate}
          onChange={(event) => setBirthdate(event.target.value)}
        />
      </Field>

      <Field label={`What does ${name} love?`} hint="Separate with commas. Skippable.">
        <TextInput
          name="interests"
          placeholder="dinosaurs, the sea"
          value={interests}
          onChange={(event) => setInterests(event.target.value)}
        />
      </Field>

      <Field
        label={`What is ${name} a little scared of?`}
        hint="We handle these gently — never as a scare."
      >
        <TextInput
          name="fears"
          placeholder="the dark"
          value={fears}
          onChange={(event) => setFears(event.target.value)}
        />
      </Field>

      <Field label="Anything to avoid entirely?" hint="These never appear in a story.">
        <TextInput
          name="avoid_topics"
          placeholder="hospitals"
          value={avoid}
          onChange={(event) => setAvoid(event.target.value)}
        />
      </Field>

      {/* The hint doubles as the slider's read-out, so the number the parent is
          choosing always has words attached to it. */}
      <Field label="How adventurous should stories get?" hint={INTENSITY_LABELS[intensity]}>
        <input
          type="range"
          name="intensity_ceiling"
          min={0}
          max={3}
          step={1}
          className="w-full"
          value={intensity}
          onChange={(event) => setIntensity(Number(event.target.value))}
        />
      </Field>

      <Field label="How long is one sitting?" hint="The story winds down warmly at the cap.">
        <select
          name="session_cap_min"
          className="w-full rounded-md border border-border bg-surface px-3 py-2 text-3"
          value={sessionCap}
          onChange={(event) => setSessionCap(Number(event.target.value))}
        >
          {[5, 10, 15, 20, 25, 30].map((minutes) => (
            <option key={minutes} value={minutes}>
              {minutes} minutes
            </option>
          ))}
        </select>
      </Field>

      <div className="flex items-center gap-3">
        <PrimaryButton type="submit" disabled={createChild.isPending}>
          {createChild.isPending ? 'Saving…' : 'Finish'}
        </PrimaryButton>
        <QuietButton type="button" onClick={() => onDone(null)}>
          Skip for now
        </QuietButton>
      </div>

      <Notice>{error}</Notice>
    </form>
  );
}
