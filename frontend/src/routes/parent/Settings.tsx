import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import { withDefaults } from '@/design/accessibility';
import {
  errorMessage,
  type ChildSettings,
  type PatchedChildSettingsRequest,
} from '@/lib/api/client';
import { useChildSettings, useUpdateChildSettings } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { useDashboardChild } from './dashboard';
import { Card, Field, Notice, PrimaryButton, QuietButton, Section, TextInput } from './ui';

/**
 * Settings & controls (PRODUCT.md §12.7, §7.13, §13.4, §13.6).
 *
 * Everything a parent can decide about one child, on one page, with no tabs and
 * nothing hidden behind an "advanced" disclosure. §12.7 asks for *prominent,
 * simple, and honored immediately*, and the third of those is the one this page
 * has to be honest about rather than merely claim: **every control here saves on
 * change**, and the value that comes back is the value the safety layer will
 * enforce. There is no Save button to forget, and no draft state that could sit
 * unapplied while a child is playing.
 *
 * That is also why the server's response replaces the form's state rather than the
 * form keeping what the parent typed: if the backend normalises something — a
 * duplicate topic, an out-of-range ceiling from an older row — the parent sees what
 * is actually in force. A settings page that echoed the input back would be a page
 * that can lie about a safety setting.
 *
 * **What is deliberately absent.** No "recommended" badge, no nudge toward a
 * longer cap or a higher ceiling, no consequence framing ("your child may enjoy
 * more time"). Every control on this page only ever restricts, and the copy says
 * what each one does without arguing for a value (§13.6, §2.6). The one persuasive
 * sentence on the page argues *for* the limit.
 */

/** The ceiling rungs, in the parent's words. Mirrors `assembler.INTENSITY_GUIDANCE`. */
const CEILINGS = [
  { value: 0, label: 'Gentlest', hint: 'Wonder and warmth only. No peril of any kind.' },
  { value: 1, label: 'Mild', hint: 'Small problems, solved quickly and kindly.' },
  { value: 2, label: 'Moderate', hint: 'Real stakes, resolved in the same scene. Nobody is hurt.' },
  { value: 3, label: 'Adventurous', hint: 'Suspense and bigger obstacles, always ending safely.' },
] as const;

const CAP_CHOICES = [5, 10, 15, 20, 25, 30] as const;

/** The accessibility toggles (§14.6), and what each actually changes. */
const ACCESSIBILITY = [
  {
    key: 'captions',
    label: 'Captions',
    hint: 'Show everything spoken as text on screen. Stays on even if story text is off.',
  },
  {
    key: 'reduced_motion',
    label: 'Reduced motion',
    hint: 'Still scenes — no transitions, drifting or breathing.',
  },
  {
    key: 'dyslexia_font',
    label: 'Dyslexia-friendly font',
    hint: 'Wider letter and word spacing, in a more readable face.',
  },
  {
    key: 'high_contrast',
    label: 'High contrast',
    hint: 'Strong dark-on-light text and hard edges. Pictures keep their own colour.',
  },
] as const;

export default function Settings() {
  const { childId } = useParams();
  const child = useDashboardChild(childId);
  const { data, isLoading, error } = useChildSettings(childId);
  const save = useUpdateChildSettings(childId);

  return (
    <DashboardShell
      child={child ?? null}
      title={child ? `${child.first_name}'s settings` : 'Settings'}
      subtitle="Every change here takes effect on the next story. There is nothing to save."
    >
      <Section isLoading={isLoading || child === undefined} error={error}>
        {data ? <SettingsForm settings={data} save={save} /> : null}
      </Section>
    </DashboardShell>
  );
}

function SettingsForm({
  settings,
  save,
}: {
  settings: ChildSettings;
  save: ReturnType<typeof useUpdateChildSettings>;
}) {
  const access = withDefaults(settings.accessibility);
  const patch = (payload: PatchedChildSettingsRequest) => save.mutate(payload);

  return (
    <div className="grid gap-6">
      {save.error ? <Notice>{errorMessage(save.error, 'That change did not save.')}</Notice> : null}

      <Card>
        <h2 className="text-4 font-semibold">How intense a story may get</h2>
        <p className="mt-1 text-2 text-text-muted">
          Enforced twice: the story is written under this rule, and every beat is checked against it
          again before your child sees it.
        </p>
        <div className="mt-4 grid gap-2">
          {CEILINGS.map((rung) => (
            <label
              key={rung.value}
              className={`flex cursor-pointer gap-3 rounded-md border p-3 ${
                settings.intensity_ceiling === rung.value ? 'border-primary bg-bg' : 'border-border'
              }`}
            >
              <input
                type="radio"
                name="intensity_ceiling"
                className="mt-1"
                checked={settings.intensity_ceiling === rung.value}
                onChange={() => patch({ intensity_ceiling: rung.value })}
              />
              <span>
                <span className="block text-3 font-semibold">{rung.label}</span>
                <span className="block text-2 text-text-muted">{rung.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </Card>

      <TopicList
        title="Topics to never include"
        hint="Never depicted, named, or alluded to. This is the one control that means never rather than less — a beat mentioning any of these is blocked, not softened."
        testId="avoid-topics"
        values={settings.avoid_topics ?? []}
        onChange={(avoid_topics) => patch({ avoid_topics })}
      />

      <TopicList
        title="Things they are frightened of"
        hint="Handled gently rather than removed: these may appear only with the companion there, with your child in control, and always resolved warmly before the scene ends."
        testId="fears"
        values={settings.fears ?? []}
        onChange={(fears) => patch({ fears })}
      />

      <Card>
        <h2 className="text-4 font-semibold">How long one sitting lasts</h2>
        <p className="mt-1 text-2 text-text-muted">
          At the cap the companion finishes the story properly and says goodbye — nothing is ever
          cut off mid-scene. Several stories in a row count as one sitting.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {CAP_CHOICES.map((minutes) => (
            <button
              key={minutes}
              type="button"
              onClick={() => patch({ session_cap_min: minutes })}
              className={`min-h-target rounded-md border px-4 py-2 text-2 ${
                settings.session_cap_min === minutes
                  ? 'border-primary bg-surface font-semibold text-primary'
                  : 'border-border text-text-muted'
              }`}
            >
              {minutes} min
            </button>
          ))}
        </div>
      </Card>

      <TimeWindow settings={settings} onChange={patch} />

      <Card>
        <h2 className="text-4 font-semibold">Voice and text</h2>
        <div className="mt-4 grid gap-3">
          <Toggle
            label="Read the story aloud"
            hint="The companion narrates every beat. With this off, the words are always shown on screen instead."
            checked={settings.voice_on ?? true}
            onChange={(voice_on) => patch({ voice_on })}
          />
          <Toggle
            label="Show the story text"
            hint="The narration under the picture, for the grown-up in the room or a child who is starting to read."
            checked={settings.text_on ?? true}
            onChange={(text_on) => patch({ text_on })}
          />
          <Field label="Narration volume" hint="Applies to the companion's voice only.">
            <input
              type="range"
              min={0}
              max={1}
              step={0.1}
              value={access.audio_volume}
              data-testid="audio-volume"
              onChange={(event) =>
                patch({ accessibility: { audio_volume: Number(event.target.value) } })
              }
              className="w-full"
            />
          </Field>
        </div>
      </Card>

      {/* Reading is off for every child until a parent says otherwise, and the
          place they say it has to be here as well as in the profile form — a
          child learns to read *between* those two moments, and nobody revisits a
          signup form six months later. */}
      <Card>
        <h2 className="text-4 font-semibold">Reading aloud</h2>
        <p className="mt-1 text-2 text-text-muted">
          When your child can read, their companion will sometimes ask them to read a short line out
          loud at the end of a story. It is never marked, and they are never told they got it wrong.
        </p>
        <div className="mt-4 grid gap-3">
          <Toggle
            label="My child can read"
            hint="Off means the companion never asks. Turn it on when they are starting to read simple sentences — around five."
            checked={!(settings.pre_reader ?? true)}
            onChange={(canRead) => patch({ pre_reader: !canRead })}
          />
          <Toggle
            label="Keep the recordings"
            hint="We save your child reading so we can check the app heard them correctly. It stays on our own machine and is never sent anywhere. Turning this off deletes what we already have."
            checked={settings.reading_audio_consent ?? true}
            onChange={(reading_audio_consent) => patch({ reading_audio_consent })}
          />
        </div>
      </Card>

      <Card>
        <h2 className="text-4 font-semibold">Accessibility</h2>
        <p className="mt-1 text-2 text-text-muted">
          Each of these changes your child&rsquo;s screen the next time they open it.
        </p>
        <div className="mt-4 grid gap-3">
          {ACCESSIBILITY.map((flag) => (
            <Toggle
              key={flag.key}
              label={flag.label}
              hint={flag.hint}
              checked={access[flag.key]}
              onChange={(value) => patch({ accessibility: { [flag.key]: value } })}
            />
          ))}
        </div>
      </Card>

      {/* The one persuasive sentence on the page, and it argues for the limit. */}
      <p className="text-1 text-text-muted">
        The Primer never sends your child a notification, a reminder or a streak, and there is
        nothing here that can turn one on. The controls on this page only ever make their time with
        us shorter.
      </p>
    </div>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer gap-3 rounded-md border border-border p-3">
      <input
        type="checkbox"
        className="mt-1"
        checked={checked}
        aria-label={label}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        <span className="block text-3 font-semibold">{label}</span>
        <span className="block text-2 text-text-muted">{hint}</span>
      </span>
    </label>
  );
}

/**
 * The daily window (§13.6).
 *
 * Held as local state and saved on blur rather than on every keystroke, because a
 * partially-typed time is a `400` from the server's both-or-neither rule and the
 * parent would watch an error appear as they typed the hour. Clearing both is how
 * "any time" is expressed, and the button says so rather than leaving a parent to
 * discover that two empty fields mean something.
 */
function TimeWindow({
  settings,
  onChange,
}: {
  settings: ChildSettings;
  onChange: (payload: PatchedChildSettingsRequest) => void;
}) {
  const [start, setStart] = useState(settings.time_window_start ?? '');
  const [end, setEnd] = useState(settings.time_window_end ?? '');

  // Re-sync when the server answers, so a normalised value replaces what was typed.
  useEffect(() => {
    setStart(settings.time_window_start ?? '');
    setEnd(settings.time_window_end ?? '');
  }, [settings.time_window_start, settings.time_window_end]);

  const halfSet = Boolean(start) !== Boolean(end);

  function commit() {
    if (halfSet) return;
    onChange({
      time_window_start: start || null,
      time_window_end: end || null,
    });
  }

  return (
    <Card>
      <h2 className="text-4 font-semibold">When stories are available</h2>
      <p className="mt-1 text-2 text-text-muted">
        Outside these hours the companion is having a rest. A story already under way is always
        allowed to finish.
      </p>
      <div className="mt-4 flex flex-wrap items-end gap-4">
        <Field label="From">
          <TextInput
            type="time"
            value={start}
            data-testid="window-start"
            onChange={(event) => setStart(event.target.value)}
            onBlur={commit}
          />
        </Field>
        <Field label="Until">
          <TextInput
            type="time"
            value={end}
            data-testid="window-end"
            onChange={(event) => setEnd(event.target.value)}
            onBlur={commit}
          />
        </Field>
        <QuietButton
          type="button"
          onClick={() => {
            setStart('');
            setEnd('');
            onChange({ time_window_start: null, time_window_end: null });
          }}
        >
          Allow any time
        </QuietButton>
      </div>
      {halfSet ? <Notice>A window needs both a start and an end.</Notice> : null}
      {!start && !end ? (
        <p className="mt-3 text-2 text-text-muted">Stories can be played at any time.</p>
      ) : null}
    </Card>
  );
}

/**
 * A short list of parent-typed phrases (avoid-topics, fears).
 *
 * Chips with a remove button rather than a comma-separated text field: these
 * strings are copied verbatim into the safety layer, and a parent must be able to
 * see exactly what is in the list and remove exactly one of them. A text field
 * hides a stray comma inside a rule that is then silently enforced.
 */
function TopicList({
  title,
  hint,
  testId,
  values,
  onChange,
}: {
  title: string;
  hint: string;
  testId: string;
  values: string[];
  onChange: (values: string[]) => void;
}) {
  const [draft, setDraft] = useState('');

  function add() {
    const term = draft.trim();
    if (!term) return;
    // The server dedupes too; doing it here as well means the parent never
    // watches an entry they just added silently vanish.
    if (!values.some((value) => value.toLowerCase() === term.toLowerCase())) {
      onChange([...values, term]);
    }
    setDraft('');
  }

  return (
    <Card>
      <h2 className="text-4 font-semibold">{title}</h2>
      <p className="mt-1 text-2 text-text-muted">{hint}</p>

      <ul data-testid={testId} className="mt-4 flex flex-wrap gap-2">
        {values.map((value) => (
          <li
            key={value}
            className="flex items-center gap-2 rounded-full border border-border px-3 py-1 text-2"
          >
            <span>{value}</span>
            <button
              type="button"
              aria-label={`Remove ${value}`}
              onClick={() => onChange(values.filter((item) => item !== value))}
              className="text-text-muted"
            >
              ✕
            </button>
          </li>
        ))}
        {values.length === 0 ? <li className="text-2 text-text-muted">Nothing listed.</li> : null}
      </ul>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1">
          <Field label={`Add to "${title.toLowerCase()}"`}>
            <TextInput
              value={draft}
              maxLength={60}
              data-testid={`${testId}-input`}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  add();
                }
              }}
            />
          </Field>
        </div>
        <PrimaryButton type="button" onClick={add} disabled={!draft.trim()}>
          Add
        </PrimaryButton>
      </div>
    </Card>
  );
}
