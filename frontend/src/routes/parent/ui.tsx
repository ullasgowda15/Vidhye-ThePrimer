/**
 * Small building blocks for the parent surface (PRODUCT.md §14.1, §15.12).
 *
 * The parent tree is "honest and calm": labelled inputs, visible errors, no
 * dark patterns. These exist so every parent screen looks the same without each
 * one re-typing the same Tailwind token strings — not as a design system, which
 * arrives properly with the dashboard.
 */

import { cloneElement, useId, type ReactElement, type ReactNode } from 'react';

import { STATUS_LABEL, STATUS_STYLE } from './dashboard';

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
      <div className="mt-4">
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
    <div data-theme="calm" className="min-h-full bg-bg text-text">
      <main className="mx-auto max-w-[34rem] p-6">
        <header className="border-b border-border pb-4">
          <h1 className="font-text text-5 font-semibold">{title}</h1>
          {subtitle ? <p className="mt-2 text-2 text-text-muted">{subtitle}</p> : null}
        </header>
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

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className="w-full rounded-md border border-border bg-surface px-3 py-2 text-3 outline-none focus:border-primary"
    />
  );
}

export function PrimaryButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className="rounded-md bg-primary px-5 py-2 text-3 font-semibold text-surface disabled:opacity-60"
    />
  );
}

export function QuietButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      className="rounded-md border border-border px-4 py-2 text-2 text-text-muted"
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
