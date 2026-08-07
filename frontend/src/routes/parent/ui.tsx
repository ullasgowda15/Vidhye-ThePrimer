/**
 * Small building blocks for the parent surface (PRODUCT.md §14.1, §15.12).
 *
 * The parent tree is "honest and calm": labelled inputs, visible errors, no
 * dark patterns. These exist so every parent screen looks the same without each
 * one re-typing the same Tailwind token strings — not as a design system, which
 * arrives properly with the dashboard.
 */

import { cloneElement, useId, type ReactElement, type ReactNode } from 'react';

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
