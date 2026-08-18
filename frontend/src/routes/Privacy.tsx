import { Link } from 'react-router-dom';

import { usePrivacyDisclosure } from '../lib/api/queries';
import { FOCUS_RING } from './parent/ui';

/**
 * The public privacy statement (PRODUCT.md §12.8, §13.8).
 *
 * **The gap this closes.** `apps/accounts/privacy.py` has always been readable
 * without an account — `PrivacyDisclosureView` is `AllowAny` with no
 * authentication classes, and says in its own docstring that "a privacy statement
 * behind a login is a privacy statement written for people who have already
 * agreed". But the only page rendering it was `/parent/privacy`, which sits under
 * `AdultGateRoute` *and* `ProtectedRoute`. So the text was public and the reading
 * of it was not: a parent evaluating the product had to create an account, and
 * hand over a child's details, to find out what we would do with them. `Landing`
 * carried a comment calling that a real gap. This is the route it asked for.
 *
 * **Deliberately not `DashboardShell`.** That shell renders per-child navigation
 * and a sign-out control and assumes a session; mounting it here would call for a
 * signed-in parent on a page whose entire purpose is being readable by someone who
 * is not one. This page therefore has its own minimal chrome and makes exactly one
 * request — the unauthenticated disclosure — and no other.
 *
 * **Same source as the gated page.** Both render `usePrivacyDisclosure()`. There
 * is no second copy of the text in the frontend, because a public statement and a
 * private one that drift apart is worse than having only the private one: it makes
 * the public copy a specific false claim rather than a missing page.
 *
 * The body is rendered as *text*, never as HTML, for the same reason the parent
 * page does it: this is copy about trustworthiness and it will not be the thing
 * that introduces an injection sink.
 */
export default function PublicPrivacy() {
  const { data: disclosure, isLoading, error } = usePrivacyDisclosure();

  return (
    <div data-theme="calm" className="min-h-full bg-bg text-text">
      <div className="mx-auto flex max-w-[46rem] flex-col gap-8 px-6 py-12">
        <header className="flex flex-col gap-3">
          <Link
            to="/"
            className={`self-start rounded-sm text-1 text-text-muted underline underline-offset-4 ${FOCUS_RING}`}
          >
            The Primer
          </Link>
          <h1 className="font-display text-6 font-semibold leading-tight">
            What we do with your child&rsquo;s data
          </h1>
          <p className="text-3 text-text-muted">
            {disclosure?.headline ?? 'What we collect, why, and what we will never do with it.'}
          </p>
        </header>

        {isLoading ? <p className="text-2 text-text-muted">Loading&hellip;</p> : null}

        {error ? (
          <div className="rounded-lg border border-border bg-surface p-5">
            <p className="text-2">
              We could not load this page just now. That is our fault, not yours — please try again
              in a moment.
            </p>
          </div>
        ) : null}

        {disclosure ? (
          <div className="flex flex-col gap-5">
            {disclosure.sections.map((section) => (
              <section key={section.id} className="rounded-lg border border-border bg-surface p-5">
                <h2 className="text-4 font-semibold">{section.title}</h2>
                {section.body.split('\n\n').map((paragraph) => (
                  <p key={paragraph.slice(0, 32)} className="mt-3 text-2 text-text-muted">
                    {paragraph}
                  </p>
                ))}
              </section>
            ))}
            <p className="text-1 text-text-muted">
              This statement was last changed on {disclosure.version}.
            </p>
          </div>
        ) : null}

        <footer className="flex flex-col gap-2 border-t border-border pt-6">
          <p className="text-1 text-text-muted">
            If you already have an account, your export and deletion controls are in{' '}
            <Link to="/parent" className={`rounded-sm underline underline-offset-4 ${FOCUS_RING}`}>
              the parent area
            </Link>
            .
          </p>
        </footer>
      </div>
    </div>
  );
}
