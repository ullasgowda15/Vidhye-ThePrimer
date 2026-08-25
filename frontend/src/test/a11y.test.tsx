/**
 * The accessibility pass (§14.6) — Day 14 criterion 4.
 *
 * The criterion has two halves and they need different kinds of test.
 *
 * *The five parent-set options work.* Captions, adjustable audio, reduced motion,
 * high contrast and the dyslexia font were built on Day 13; what this file adds is
 * proof that each one still reaches the thing it is supposed to change. `settings
 * .test.tsx` covers the controls; these cover the **effect**, which is the half
 * that can rot silently — a flag whose CSS block was renamed still toggles
 * perfectly and does nothing.
 *
 * *The parent UI meets WCAG AA basics.* Contrast is computed from `tokens.css`
 * itself, exactly as `worlds.test.tsx` does for the child palettes and for the
 * same reason: this is where a value regresses, and a rendered-DOM check would
 * pass on a stylesheet nobody had loaded. Keyboard and focus are asserted against
 * the shared components in `routes/parent/ui.tsx`, which is the only place on that
 * surface an interactive element comes from.
 *
 * The two calm-theme colours this suite was written against were both failing:
 * `--accent` at 3.95:1 and `--success` at 4.06:1, rendered as the words
 * "practising" and "mastered" at 14px on every insights card.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import {
  DEFAULT_ACCESSIBILITY,
  safeVolume,
  useAccessibility,
  withDefaults,
  wordsOnScreen,
} from '@/design/accessibility';
import {
  Field,
  LinkButton,
  PasswordInput,
  PrimaryButton,
  QuietButton,
  Section,
  SkipLink,
  TextInput,
} from '@/routes/parent/ui';

const tokensCss = readFileSync(resolve(process.cwd(), 'src/design/tokens.css'), 'utf8');

/**
 * `tokens.css` with its comments removed.
 *
 * Not a tidy-up. `block` below splits a declaration block on `;` and keeps the
 * chunks that start with `--`, and most of the blocks this suite reads open with
 * a comment — so the declaration immediately *after* each comment arrives as
 * "/* … *\/ --primitive-cream: #fdf6e9" and is silently dropped. That is how the
 * first version of this file managed to report `--bg` as undefined while looking
 * straight at it. `worlds.test.tsx` has the same parser and gets away with it
 * only because the world palettes happen to carry no inline comments.
 */
const tokensWithoutComments = tokensCss.replace(/\/\*[\s\S]*?\*\//g, '');

/** The custom properties declared inside `selector { … }`, as `{token: value}`. */
function block(selector: string): Record<string, string> {
  const pattern = selector
    .replace(/[[\]=]/g, (char) => `\\${char}`)
    .replace(/'([^']*)'/g, `['"]$1['"]`);
  const match = new RegExp(`${pattern}\\s*\\{([^}]*)\\}`).exec(tokensWithoutComments);
  if (!match) return {};
  return Object.fromEntries(
    match[1]
      .split(';')
      .map((line) => line.trim())
      .filter((line) => line.startsWith('--'))
      .map((line) => {
        const [token, ...rest] = line.split(':');
        return [token.trim(), rest.join(':').trim()];
      }),
  );
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Resolve a `--token` in the calm theme down to a literal `#rrggbb`.
 *
 * The calm block is written in terms of the primitives (`var(--primitive-teal)`),
 * so a test that read it without following the indirection would be asserting on
 * the string "var(--primitive-teal)" and passing forever.
 */
const root = block(':root');
const calm = block("[data-theme='calm']");

function calmValue(token: string): string {
  const declared = calm[token] ?? root[token];
  const reference = /^var\((--[a-z-]+)\)$/.exec(declared ?? '');
  const resolved = reference ? (root[reference[1]] ?? calm[reference[1]]) : declared;
  if (!resolved?.startsWith('#')) {
    throw new Error(`${token} did not resolve to a hex value (got ${resolved})`);
  }
  return resolved;
}

afterEach(() => {
  for (const name of ['data-a11y-motion', 'data-a11y-font', 'data-a11y-contrast']) {
    document.documentElement.removeAttribute(name);
  }
});

// ---------------------------------------------------------------------------
// WCAG AA on the parent surface
// ---------------------------------------------------------------------------

describe('the parent theme meets WCAG AA', () => {
  /**
   * Every token the parent surface renders as *text*, and what it renders on.
   *
   * `--accent` and `--success` are in here because `dashboard.ts` puts them on the
   * status words with `text-accent` / `text-success`. A reader who assumed they
   * were decoration would leave them out, which is exactly how they shipped below
   * AA in the first place.
   */
  const textTokens = ['--text', '--text-muted', '--primary', '--accent', '--success'];

  it.each(textTokens)('renders %s at 4.5:1 or better on the page background', (token) => {
    // 4.5:1 is AA for normal-sized text, and everything in this list appears at
    // 14–18px somewhere on the dashboard. The large-text 3:1 allowance would be
    // the wrong yardstick: the status pills are `text-1`, which is 14px.
    expect(contrast(calmValue(token), calmValue('--bg'))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(textTokens)('renders %s at 4.5:1 or better on a card', (token) => {
    expect(contrast(calmValue(token), calmValue('--surface'))).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps the primary button legible against its own fill', () => {
    // The one pair that is not text-on-background: `PrimaryButton` is
    // `bg-primary text-surface`.
    expect(contrast(calmValue('--surface'), calmValue('--primary'))).toBeGreaterThanOrEqual(4.5);
  });

  it('draws a focus indicator with enough contrast to see', () => {
    // WCAG 1.4.11 asks 3:1 for a non-text indicator. The ring is `--primary`, and
    // its offset means it is drawn against `--bg`, not against the control.
    expect(contrast(calmValue('--primary'), calmValue('--bg'))).toBeGreaterThanOrEqual(3);
  });

  it('gives the parent surface a touch target floor', () => {
    // §14.6 asks for targets "well above minimum". 44px is the platform floor and
    // is what the calm theme sets; the child theme's 64px is checked by the child
    // suite. A theme that dropped the token would silently un-size every button.
    expect(calm['--target-min']).toBe('44px');
  });
});

// ---------------------------------------------------------------------------
// Keyboard, focus and labelling
// ---------------------------------------------------------------------------

describe('the parent controls are usable from a keyboard', () => {
  it('gives every interactive component a visible focus ring', () => {
    render(
      <div>
        <PrimaryButton type="button">Save</PrimaryButton>
        <QuietButton type="button">Cancel</QuietButton>
        <Field label="First name">
          <TextInput name="first_name" />
        </Field>
      </div>,
    );

    for (const element of [
      screen.getByRole('button', { name: 'Save' }),
      screen.getByRole('button', { name: 'Cancel' }),
      screen.getByRole('textbox', { name: 'First name' }),
    ]) {
      // `focus-visible:` rather than `focus:`, so a mouse user never sees a ring
      // and a keyboard user always does.
      expect(element.className).toContain('focus-visible:outline-primary');
      expect(element.className).toContain('focus-visible:outline-offset-2');
    }
  });

  it('puts a skip link first, hidden until it is focused', () => {
    render(<SkipLink />);

    const link = screen.getByRole('link', { name: 'Skip to content' });
    expect(link).toHaveAttribute('href', '#main-content');
    // Off-screen by transform, and brought back by `focus:` — not `hidden`, which
    // would take it out of the tab order and defeat the whole point.
    expect(link.className).toContain('-translate-y-24');
    expect(link.className).toContain('focus:translate-y-0');
  });

  it('routes the first-run call to action without reloading the app', () => {
    // `LinkButton` shipped as a bare `<a href>`, which inside a SPA is a full
    // document navigation: white flash, remount, query cache discarded — on the
    // one button of the one screen a parent sees before anything else exists
    // (Day-14 review, P1-6). A router `Link` still renders a single `<a>`, so the
    // reason the component exists — never `<Link><button>`, which announces two
    // controls for one thing (WCAG 4.1.2) — is untouched.
    render(
      <MemoryRouter>
        <LinkButton to="/parent/onboarding">Add your first child</LinkButton>
      </MemoryRouter>,
    );

    const link = screen.getByRole('link', { name: 'Add your first child' });
    expect(link.tagName).toBe('A');
    expect(link.querySelector('button')).toBeNull();
    expect(link).toHaveAttribute('href', '/parent/onboarding');
    // The target floor and the focus ring cannot be opted out of by a caller.
    expect(link.className).toContain('min-h-[--target-min]');
    expect(link.className).toContain('focus-visible:outline-primary');
  });

  it('reveals a password without losing the field its label', async () => {
    // Typing a password you cannot see is a guess, and on this surface a mistyped
    // one locks a parent out of their own child's data. The toggle has to do that
    // without costing the field anything it already had: `Field` names the input
    // through `cloneElement`, so an `id` landing on the wrapper instead of the
    // `<input>` would leave the label pointing at nothing.
    const user = userEvent.setup();
    render(
      <Field label="Password" hint="At least eight characters.">
        <PasswordInput name="password" />
      </Field>,
    );

    // Hidden: no `textbox` role, so it has to be found by its label.
    const input = screen.getByLabelText('Password');
    expect(input.tagName).toBe('INPUT');
    expect(input).toHaveAttribute('type', 'password');
    expect(input).toHaveAccessibleDescription('At least eight characters.');

    // `type="button"`, or revealing the password submits the form it sits in.
    const toggle = screen.getByRole('button', { name: 'Show password' });
    expect(toggle).toHaveAttribute('type', 'button');

    await user.click(toggle);
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text');
    // The name says what the next press does, and it is the only place the state
    // is announced — `aria-pressed` as well would say it twice, in opposite
    // directions ("Hide password, pressed").
    expect(toggle).toHaveAccessibleName('Hide password');
    expect(toggle).not.toHaveAttribute('aria-pressed');

    await user.click(toggle);
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
  });

  it('names a field without swallowing its hint into the accessible name', () => {
    // The failure this guards against is subtle and common: wrapping both the
    // label and the hint in the `<label>` makes a screen reader announce the field
    // as "First name Or the nickname the companion should use" — technically
    // labelled, practically unusable.
    render(
      <Field label="First name" hint="Or the nickname the companion should use.">
        <TextInput name="first_name" />
      </Field>,
    );

    const input = screen.getByRole('textbox', { name: 'First name' });
    expect(input).toHaveAccessibleDescription('Or the nickname the companion should use.');
  });

  it('announces a section as it loads, fails and settles', () => {
    // Without a live region the parent surface is silent through every transition
    // and a section simply *is* whatever it was — which here means "your child did
    // nothing", the exact impression §12.6 exists to avoid.
    const { container, rerender } = render(
      <Section title="Reports" isLoading>
        <p>done</p>
      </Section>,
    );
    const live = container.querySelector('[aria-live="polite"]');
    expect(live).toHaveAttribute('aria-busy', 'true');

    rerender(
      <Section title="Reports">
        <p>done</p>
      </Section>,
    );
    expect(container.querySelector('[aria-live="polite"]')).toHaveAttribute('aria-busy', 'false');
  });

  it('shows an error rather than an empty panel', () => {
    // The parent surface is the one place in the product that *shows* failures
    // (§13 forbids it on the child's screen). `role="alert"` is what makes that
    // true for a screen reader too.
    render(
      <Section title="Reports" error={new Error('boom')}>
        <p>never rendered</p>
      </Section>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('We could not load this');
    expect(screen.queryByText('never rendered')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The five parent-set options still reach something
// ---------------------------------------------------------------------------

describe('every accessibility option changes something real', () => {
  function Harness({ settings }: { settings: Parameters<typeof useAccessibility>[0] }) {
    useAccessibility(settings);
    return <p>story</p>;
  }

  it.each([
    ['reduced_motion', 'data-a11y-motion'],
    ['dyslexia_font', 'data-a11y-font'],
    ['high_contrast', 'data-a11y-contrast'],
  ] as const)('puts %s on the document root as %s', (flag, attribute) => {
    const { unmount } = render(<Harness settings={{ [flag]: true }} />);
    expect(document.documentElement.getAttribute(attribute)).toBe('on');

    // Cleared on unmount, because these routes are per-child and a parent walking
    // from one child's story to another's must not carry the first child's
    // settings with them.
    unmount();
    expect(document.documentElement.getAttribute(attribute)).toBeNull();
  });

  it('has a stylesheet rule behind each of those three attributes', () => {
    // The half that rots quietly. A flag whose CSS block was renamed still
    // toggles perfectly and changes nothing on screen, and no DOM assertion
    // above would notice.
    expect(block(":root[data-a11y-motion='on']")['--motion-base']).toBe('0ms');

    const font = block(":root[data-a11y-font='on']");
    expect(font['--font-display']).toContain('OpenDyslexic');
    expect(font['--font-text']).toBe('var(--font-display)');

    const contrastBlock = block(":root[data-a11y-contrast='on']");
    expect(contrastBlock['--text']).toBe('#000000');
    expect(contrastBlock['--bg']).toBe('#ffffff');
  });

  it('makes high contrast clear AA by a wide margin, in both light and night', () => {
    const light = block(":root[data-a11y-contrast='on']");
    const night = block(":root[data-a11y-contrast='on'][data-theme='night']");

    for (const palette of [light, night]) {
      for (const token of ['--text', '--text-muted', '--primary', '--success']) {
        // AAA (7:1), not AA: a parent turns this on for a child who needs it, and
        // "just about legal" is not the point of the setting.
        expect(contrast(palette[token], palette['--bg'])).toBeGreaterThanOrEqual(7);
      }
    }
  });

  it('lets high contrast win over a world palette', () => {
    // Declaration order in `tokens.css` is the mechanism, and it is the one case
    // where the setting would otherwise silently do nothing: a parent who turned
    // it on must get it in the Coral Deep as well as on the base theme.
    const contrastAt = tokensCss.indexOf("[data-a11y-contrast='on']");
    const lastWorldAt = tokensCss.lastIndexOf("[data-world='");
    expect(contrastAt).toBeGreaterThan(lastWorldAt);
  });

  it('keeps captions on by default and lets them outrank a text preference', () => {
    expect(DEFAULT_ACCESSIBILITY.captions).toBe(true);

    // §14.6 guarantees an on-screen equivalent for everything spoken, and it
    // outranks the parent's taste about the story paragraph.
    expect(
      wordsOnScreen({ text_on: false, voice_on: true, accessibility: { captions: true } }),
    ).toBe(true);
    expect(
      wordsOnScreen({ text_on: false, voice_on: true, accessibility: { captions: false } }),
    ).toBe(false);
    // A silent voice outranks both: a beat delivered as neither sound nor text is
    // not a beat.
    expect(
      wordsOnScreen({ text_on: false, voice_on: false, accessibility: { captions: false } }),
    ).toBe(true);
  });

  it('clamps volume into the range the audio element will accept', () => {
    // An out-of-range assignment throws `IndexSizeError`, and a throw here is an
    // uncaught error between a child and their story.
    expect(safeVolume(1.5)).toBe(1);
    expect(safeVolume(-1)).toBe(0);
    expect(safeVolume(Number.NaN)).toBe(DEFAULT_ACCESSIBILITY.audio_volume);
    expect(safeVolume(undefined)).toBe(DEFAULT_ACCESSIBILITY.audio_volume);
    expect(safeVolume(0.4)).toBe(0.4);
  });

  it('fills a partial settings blob with the accessible answers', () => {
    // The defaults have to be right while the settings request is still in
    // flight: a child's first beat must not arrive silent because a fetch had not
    // landed.
    expect(withDefaults(undefined)).toEqual(DEFAULT_ACCESSIBILITY);
    expect(withDefaults({ high_contrast: true })).toEqual({
      ...DEFAULT_ACCESSIBILITY,
      high_contrast: true,
    });
  });

  it('honours the OS reduced-motion setting as well as the parent flag', () => {
    // Neither switch may re-enable what the other turned off, so both blocks
    // exist and both zero the same tokens.
    expect(tokensCss).toContain('@media (prefers-reduced-motion: reduce)');
    expect(block(":root[data-a11y-motion='on']")['--motion-base']).toBe('0ms');
  });
});
