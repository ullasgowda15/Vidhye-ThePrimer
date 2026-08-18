/**
 * Choose a companion (PRODUCT.md §9.1, §14.5, §4.2).
 *
 * The last step of onboarding, and the only one a *child* should be leaning
 * over the parent's shoulder for — naming is the bonding act, so the name field
 * asks for what the child wants to call them, not for a product decision.
 *
 * Three choices, each with an excellent default: a §9.1 sixty-second setup must
 * still produce a lovable companion. Picking an archetype fills in the palette
 * and temperament that suit it, so "tap a creature, type a name, done" is a
 * complete path through this screen.
 *
 * The options come from the API rather than a list in this file: the same
 * catalogue validates the request, so a picker showing an archetype the backend
 * would reject is not possible.
 */

import { useEffect, useState } from 'react';

import {
  errorMessage,
  type ArchetypeKey,
  type ArchetypeOption,
  type PaletteColors,
  type PaletteKey,
  type PersonalityPresetKey,
} from '@/lib/api/client';
import { useCompanionCatalog, useCreateCompanion } from '@/lib/api/queries';

import { Field, Notice, PrimaryButton, TextInput } from './ui';

export default function CompanionPicker({
  childId,
  childName,
  onDone,
}: {
  childId: string;
  childName: string;
  onDone: (childId: string) => void;
}) {
  const { data: catalog } = useCompanionCatalog();
  const createCompanion = useCreateCompanion(childId);

  const [archetype, setArchetype] = useState<ArchetypeKey | ''>('');
  const [palette, setPalette] = useState<PaletteKey | ''>('');
  const [preset, setPreset] = useState<PersonalityPresetKey | ''>('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Default to the first archetype as soon as the catalogue lands, so the
  // screen is never in a state where nothing is chosen.
  useEffect(() => {
    const first = catalog?.archetypes?.[0];
    if (first && !archetype) choose(first);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog]);

  function choose(option: ArchetypeOption) {
    setArchetype(option.key);
    setPalette(option.default_palette);
    setPreset(option.default_personality_preset);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await createCompanion.mutateAsync({
        archetype: archetype as ArchetypeKey,
        name,
        palette: palette as PaletteKey,
        personality_preset: preset as PersonalityPresetKey,
      });
      onDone(childId);
    } catch (caught) {
      setError(errorMessage(caught, 'We could not create that companion.'));
    }
  }

  const archetypes = catalog?.archetypes ?? [];
  const palettes = catalog?.palettes ?? [];
  const presets = catalog?.personality_presets ?? [];
  const chosen = archetypes.find((option) => option.key === archetype);

  return (
    <form onSubmit={submit} className="space-y-5">
      <h2 className="text-4 font-semibold">Who should meet {childName}?</h2>
      <p className="text-2 text-text-muted">
        This is the friend who will tell {childName} every story. Let them choose, and let them pick
        the name — it is theirs.
      </p>

      <fieldset>
        <legend className="text-2 font-semibold">Choose a friend</legend>
        <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {archetypes.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={option.key === archetype}
              onClick={() => choose(option)}
              className={`min-h-target rounded-lg border p-3 text-left transition-transform duration-fast ${
                option.key === archetype
                  ? 'border-primary bg-surface shadow-card scale-[1.02]'
                  : 'border-border bg-surface'
              }`}
            >
              <CompanionMark
                archetype={option.key}
                colors={palettes.find((swatch) => swatch.key === palette)}
              />
              <span className="mt-2 block text-2 font-semibold capitalize">{option.label}</span>
              <span className="mt-1 block text-1 text-text-muted">{option.blurb}</span>
            </button>
          ))}
        </div>
      </fieldset>

      <Field
        label="What is their name?"
        hint={`Ask ${childName}. Whatever they say is the right answer.`}
      >
        <TextInput
          name="name"
          required
          maxLength={40}
          placeholder="Pip"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </Field>

      <fieldset>
        <legend className="text-2 font-semibold">What colour?</legend>
        <div className="mt-2 flex flex-wrap gap-3">
          {palettes.map((swatch) => (
            <button
              key={swatch.key}
              type="button"
              aria-label={swatch.label}
              aria-pressed={swatch.key === palette}
              onClick={() => setPalette(swatch.key)}
              className={`h-11 w-11 rounded-full border-2 ${
                swatch.key === palette ? 'border-primary' : 'border-border'
              }`}
              style={{
                background: `linear-gradient(135deg, ${swatch.primary} 50%, ${swatch.secondary} 50%)`,
              }}
            />
          ))}
        </div>
      </fieldset>

      <Field
        label="How should they be?"
        hint={chosen ? `${chosen.label} is usually a good match for this one.` : undefined}
      >
        <select
          name="personality_preset"
          className="w-full rounded-md border border-border bg-surface px-3 py-2 text-3"
          value={preset}
          onChange={(event) => setPreset(event.target.value as PersonalityPresetKey)}
        >
          {presets.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>
      </Field>

      <PrimaryButton
        type="submit"
        disabled={!archetype || !name.trim() || createCompanion.isPending}
      >
        {createCompanion.isPending ? 'Waking them up…' : `Introduce them to ${childName}`}
      </PrimaryButton>

      <Notice>{error}</Notice>
    </form>
  );
}

/**
 * A tiny painted mark for each archetype.
 *
 * Deliberately drawn rather than generated: the picker is shown before any
 * companion exists, so there is nothing to illustrate yet, and a child must be
 * able to choose by *pointing at a picture* (§14.5) rather than by reading.
 */
function CompanionMark({ archetype, colors }: { archetype: string; colors?: PaletteColors }) {
  const primary = colors?.primary ?? 'var(--primary)';
  const secondary = colors?.secondary ?? 'var(--accent)';

  return (
    <svg viewBox="0 0 64 64" className="h-16 w-16" aria-hidden="true">
      <circle cx="32" cy="38" r="20" fill={primary} />
      <ellipse cx="32" cy="44" rx="12" ry="10" fill={secondary} opacity="0.8" />
      {archetype === 'dragon' && <path d="M20 20 l6 -12 l8 12 Z" fill={secondary} />}
      {archetype === 'fox' && (
        <>
          <path d="M16 24 l2 -12 l12 8 Z" fill={primary} />
          <path d="M48 24 l-2 -12 l-12 8 Z" fill={primary} />
        </>
      )}
      {archetype === 'robot' && <rect x="30" y="6" width="4" height="12" rx="2" fill={primary} />}
      {archetype === 'star' && (
        <path
          d="M32 4 l6 12 l13 2 l-9 9 l2 13 l-12 -6 l-12 6 l2 -13 l-9 -9 l13 -2 Z"
          fill={secondary}
        />
      )}
      {archetype === 'owl' && (
        <>
          <circle cx="24" cy="18" r="6" fill={secondary} />
          <circle cx="40" cy="18" r="6" fill={secondary} />
        </>
      )}
      {archetype === 'princess' && (
        <>
          {/* A crown with three blunt points, echoing the catalogue's signature
              detail. Symmetric here on purpose: the marks are flat emblems a
              child picks from, not portraits, and the crooked-crown detail is
              for the illustrator to carry, not for a 64px swatch. */}
          <path d="M20 20 l0 -10 l6 5 l6 -8 l6 8 l6 -5 l0 10 Z" fill={secondary} />
          <circle cx="32" cy="7" r="2" fill={primary} />
        </>
      )}
      <circle cx="25" cy="34" r="3.5" fill="#fffdf8" />
      <circle cx="39" cy="34" r="3.5" fill="#fffdf8" />
      <circle cx="25" cy="34" r="1.8" fill="#3a2b1f" />
      <circle cx="39" cy="34" r="1.8" fill="#3a2b1f" />
    </svg>
  );
}
