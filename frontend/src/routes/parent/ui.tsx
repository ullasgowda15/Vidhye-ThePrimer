/**
 * Small building blocks for the parent surface (PRODUCT.md §14.1, §14.6, §15.12).
 *
 * The parent tree is "honest and calm": labelled inputs, visible errors, no
 * dark patterns. These exist so every parent screen looks the same without each
 * one re-typing the same Tailwind token strings — not as a design system, which
 * arrives properly with the dashboard.
 *
 * **§14.6's WCAG AA target is enforced here, once.** Every interactive element on
 * this surface is one of the four components below, so keyboard visibility,
 * target size and the contrast of a focus indicator are properties of this file
 * rather than of the eleven pages that import it. That is the whole reason to
 * have it: an accessibility rule applied per page is an accessibility rule that
 * the twelfth page will not have.
 */

import { cloneElement, useId, useState, type ReactElement, type ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router-dom';

import { STATUS_LABEL, STATUS_STYLE } from './dashboard';

/**
 * The focus indicator, written once (§14.6, WCAG 2.4.7).
 *
 * `focus-visible` rather than `focus`, so a parent using a mouse never sees a
 * ring they did not ask for and a parent using a keyboard always does.
 *
 * The offset is not decoration: an outline drawn *on* a control's own border
 * against a same-family colour is a ring nobody can see. Two pixels of the page
 * background between the control and the ring is what makes it visible on the
 * bordered inputs, the filled primary button and the quiet outlined button
 * alike — three different backgrounds, one indicator.
 *
 * `--primary` is 6.83:1 on the calm background, which clears WCAG 1.4.11's 3:1
 * for a non-text indicator with room to spare.
 */
export const FOCUS_RING =
  'outline-none focus-visible:outline focus-visible:outline-2 ' +
  'focus-visible:outline-offset-2 focus-visible:outline-primary';

/**
 * An inline text link on the parent surface.
 *
 * The underline is not a style choice: `--primary` against `--text` is not a 3:1
 * difference, so colour alone would make links indistinguishable from body copy
 * for a parent who cannot separate the two hues (WCAG 1.4.1). The underline is
 * the signal; the colour is the emphasis.
 *
 * `rounded-sm` so the focus ring traces the link rather than boxing it squarely
 * against the surrounding text.
 */
export const LINK = `text-primary underline underline-offset-4 rounded-sm ${FOCUS_RING}`;

/**
 * Jump past the header to the page's content (WCAG 2.4.1).
 *
 * Visually hidden until it takes focus, which is the point: it is the first
 * thing a keyboard or screen-reader user reaches, and it is worth nothing to
 * anyone else. Without it, every page on this surface makes a parent tab through
 * the same eight header and nav links before reaching what they came for.
 *
 * Positioned rather than `sr-only`-toggled so it appears in place when focused
 * instead of shifting the layout under it.
 */
export function SkipLink({ to = '#main-content' }: { to?: string }) {
  return (
    <a
      href={to}
      className={`absolute left-4 top-4 z-50 -translate-y-24 rounded-md border border-border bg-surface px-4 py-2 text-2 font-semibold text-primary transition-transform focus:translate-y-0 ${FOCUS_RING}`}
    >
      Skip to content
    </a>
  );
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-border bg-surface p-5 shadow-card ${className}`}>
      {children}
    </div>
  );
}

/**
 * A block of the dashboard, with its three states written once.
 *
 * The `error` branch is the point. The child surface never shows an error (§13)
 * and this surface must — a parent reading a report about their own child is owed
 * "we could not load this" rather than an empty panel that looks like "your child
 * did nothing". Getting that backwards on one page would undo §12.6's whole
 * argument, so no page is allowed to write its own version of it.
 *
 * **The three states are announced, not only drawn** (§14.6). The panel carries
 * `aria-live="polite"` and `aria-busy`, so a parent using a screen reader is told
 * when a report has finished loading and when it failed. Without it the page is
 * silent through every transition and the section simply *is* whatever it was
 * when they last looked — which on this surface means "your child did nothing",
 * for the same reason the error branch exists.
 */
export function Section({
  title,
  hint,
  isLoading,
  error,
  isEmpty,
  emptyText,
  children,
}: {
  title?: string;
  hint?: string;
  isLoading?: boolean;
  error?: unknown;
  isEmpty?: boolean;
  emptyText?: string;
  children: ReactNode;
}) {
  return (
    <section className="mt-6">
      {title ? <h2 className="text-4 font-semibold">{title}</h2> : null}
      {hint ? <p className="mt-1 text-2 text-text-muted">{hint}</p> : null}
      <div className="mt-4" aria-live="polite" aria-busy={Boolean(isLoading)}>
        {isLoading ? <p className="text-2 text-text-muted">Loading…</p> : null}
        {!isLoading && error ? (
          <Notice>We could not load this just now. Please try again in a moment.</Notice>
        ) : null}
        {!isLoading && !error && isEmpty ? (
          <p className="text-2 text-text-muted">{emptyText ?? 'Nothing here yet.'}</p>
        ) : null}
        {!isLoading && !error && !isEmpty ? children : null}
      </div>
    </section>
  );
}

/**
 * One of the four status words (§12.3), as a word.
 *
 * There is no bar, no ring and no percentage in here, and that is a design
 * constraint rather than an unfinished component: a filled bar *is* a grade, and
 * a parent reading one at 64% would be reading something untrue about a child who
 * has finished counting to three (see `apps/analytics/skills.py`). Colour carries
 * the same four steps as the word so the page can be scanned, and never a fifth
 * meaning like "behind".
 */
export function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full border px-3 py-1 text-1 font-semibold ${
        STATUS_STYLE[status] ?? STATUS_STYLE.not_started
      }`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function ParentShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div data-theme="calm" className="relative min-h-full bg-bg text-text">
      <SkipLink />
      {/* The heading is a `banner`, so it sits outside `main` rather than inside
          it — a screen reader's landmark list should say "banner, main", not
          "main, containing a banner". */}
      <header className="mx-auto max-w-[34rem] px-6 pt-6">
        <div className="border-b border-border pb-4">
          <h1 className="font-text text-5 font-semibold">{title}</h1>
          {subtitle ? <p className="mt-2 text-2 text-text-muted">{subtitle}</p> : null}
        </div>
      </header>
      {/* `tabIndex={-1}` so the skip link can actually move focus here. An `id`
          alone moves the scroll position and leaves focus in the header, so the
          next Tab lands back at the top and the link does nothing. */}
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-[34rem] px-6 pb-6">
        <div className="mt-5">{children}</div>
      </main>
    </div>
  );
}

/**
 * A labelled control.
 *
 * The label wraps *only* the label text and points at the control by id, with
 * the hint attached as a description. Wrapping both would fold the hint into
 * the control's accessible name, so a screen reader would announce "First name
 * Or the nickname the companion should use" as the field's name — technically
 * labelled, practically unusable.
 */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactElement;
}) {
  const id = useId();
  const hintId = `${id}-hint`;

  return (
    <div>
      <label htmlFor={id} className="block text-2 font-semibold">
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="mt-1 text-1 text-text-muted">
          {hint}
        </p>
      ) : null}
      <div className="mt-2">
        {cloneElement(children as ReactElement<Record<string, unknown>>, {
          id,
          'aria-describedby': hint ? hintId : undefined,
        })}
      </div>
    </div>
  );
}

const INPUT_BASE = `w-full rounded-md border border-border bg-surface px-3 py-2 text-3 focus:border-primary ${FOCUS_RING}`;

/**
 * `className` is appended rather than replaced. A caller can add to the box —
 * `PasswordInput` reserves room on the right for its toggle — but cannot drop
 * the border, the sizing or the focus ring, which is the same bargain
 * `LinkButton` strikes.
 */
export function TextInput({
  className = '',
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${INPUT_BASE} ${className}`} />;
}

/**
 * A password field with a reveal toggle (Feather's eye / eye-off, inline because
 * this app ships no icon dependency).
 *
 * Typing a password you cannot see is a guess, and the parent surface is where a
 * mistyped one costs the most: a parent who cannot get back in cannot reach their
 * own child's data. So this is a usability fix rather than a convenience, and it
 * lives here rather than on three pages for the reason at the top of this file —
 * a control written per page is a control the fourth page gets wrong.
 *
 * **The button's accessible name changes; `aria-pressed` is deliberately absent.**
 * Carrying both makes a screen reader announce "Hide password, pressed", which
 * states the same fact twice and in opposite directions. A name that says what
 * the next press does is the pattern users already know, and because the button
 * is focused when it changes, the change is announced.
 *
 * `type="button"` is load bearing: a bare `<button>` inside a `<form>` defaults
 * to `submit`, so revealing the password would post the login form instead.
 *
 * `id` and `aria-describedby` arrive from `Field` via `cloneElement` and are
 * spread onto the `<input>`, not this wrapper, so the label still names the field
 * and the hint still describes it.
 */
export function PasswordInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const [revealed, setRevealed] = useState(false);

  return (
    <div className="relative">
      {/* `pr` clears the toggle so a long password never runs underneath it. */}
      <TextInput {...props} type={revealed ? 'text' : 'password'} className="pr-[--target-min]" />
      <button
        type="button"
        onClick={() => setRevealed((shown) => !shown)}
        aria-label={revealed ? 'Hide password' : 'Show password'}
        className={`absolute inset-y-0 right-0 flex w-[--target-min] items-center justify-center rounded-md text-text-muted ${FOCUS_RING}`}
      >
        {/* Decorative: the button is already named above, so announcing the icon
            too would say the same thing twice. */}
        <EyeIcon crossedOut={revealed} />
      </button>
    </div>
  );
}

function EyeIcon({ crossedOut }: { crossedOut: boolean }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {crossedOut ? (
        <>
          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
          <line x1="1" y1="1" x2="23" y2="23" />
        </>
      ) : (
        <>
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </>
      )}
    </svg>
  );
}

/**
 * `min-h-[--target-min]` on both buttons is §14.6's "large touch targets".
 *
 * The token resolves to 44px under `[data-theme="calm"]` and 64px on the child
 * surface — the same declaration, two answers, because a parent scanning a report
 * on a phone and a four-year-old with no fine motor control want different things
 * and neither should be the other's compromise (§15.4).
 */
export function PrimaryButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`min-h-[--target-min] rounded-md bg-primary px-5 py-2 text-3 font-semibold text-surface disabled:opacity-60 ${FOCUS_RING}`}
    />
  );
}

/**
 * A link that looks like `PrimaryButton`.
 *
 * It exists because the alternative people reach for — `<Link><PrimaryButton/></Link>` —
 * nests a `<button>` inside an `<a>`. That is invalid HTML, and it is an
 * accessibility fault rather than a pedantic one (WCAG 4.1.2): the two elements
 * are both interactive, so assistive technology has two controls to announce for
 * one thing on the screen and no way to say which of them does anything. One
 * element, one role, one name.
 *
 * **A router `Link`, not a bare `<a href>`.** It shipped as the latter and that
 * made the first-run call to action — the one button on the one screen a parent
 * sees before anything else exists — a full document navigation inside a SPA:
 * white flash, app remount, query cache discarded (Day-14 review, P1-6). `Link`
 * renders a single `<a>`, so the accessibility argument above is untouched; what
 * changes is that the transition is a transition.
 *
 * `className` is passed through rather than composed here so a caller can widen
 * it; the focus ring and target size cannot be opted out of.
 */
export function LinkButton({
  children,
  className = '',
  ...props
}: LinkProps & { children: ReactNode }) {
  return (
    <Link
      {...props}
      className={`inline-flex min-h-[--target-min] items-center rounded-md bg-primary px-5 py-2 text-3 font-semibold text-surface no-underline ${FOCUS_RING} ${className}`}
    >
      {children}
    </Link>
  );
}

export function QuietButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className={`min-h-[--target-min] rounded-md border border-border px-4 py-2 text-2 text-text-muted ${FOCUS_RING}`}
    />
  );
}

/**
 * An error a parent can act on.
 *
 * `role="alert"` on purpose — this is the surface where being told plainly what
 * went wrong is the trustworthy behaviour. The child surface does the opposite.
 */
export function Notice({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p role="alert" className="mt-3 rounded-md border border-border bg-surface p-3 text-2">
      {children}
    </p>
  );
}
