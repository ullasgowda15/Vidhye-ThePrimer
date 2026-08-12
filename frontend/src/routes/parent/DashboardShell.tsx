import { Link, NavLink, useNavigate } from 'react-router-dom';

import { useMe } from '@/lib/api/queries';
import { useAuthStore } from '@/store/auth';

import type { ChildSummary } from './dashboard';
import { QuietButton } from './ui';

/**
 * The parent dashboard's frame and its information architecture (§12.10, §15.1).
 *
 * `data-theme="calm"` is the whole theme switch: every token underneath
 * re-resolves, so the same primitives read as a quiet dashboard here and as a
 * storybook on the child route. No component branches on which surface it is in.
 *
 * §12.10's map is Home → Reports / Weekly-Growth / Insights / Transparency /
 * Settings / Privacy / Billing / Children. Five of those are built; Settings,
 * Privacy and Billing arrive on Days 13-14. They are named in a plain line at the
 * foot rather than rendered as nav items that go nowhere — a dead link in a
 * product whose headline feature is honesty is a small lie in the wrong place.
 */
const SECTIONS = [
  { to: 'reports', label: 'Reports' },
  { to: 'weekly', label: 'Weekly & growth' },
  { to: 'insights', label: 'Insights' },
  { to: 'transparency', label: 'Transparency' },
] as const;

export function DashboardShell({
  child,
  title,
  subtitle,
  children,
}: {
  /** Null on Home, where the page is about the account rather than one child. */
  child?: ChildSummary | null;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  const navigate = useNavigate();
  const { data: me } = useMe();
  const logout = useAuthStore((state) => state.logout);

  async function signOut() {
    await logout();
    navigate('/', { replace: true });
  }

  return (
    <div data-theme="calm" className="min-h-full bg-bg text-text">
      <main className="mx-auto max-w-[56rem] p-6">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
          <div>
            <h1 className="font-text text-5 font-semibold">{title}</h1>
            <p className="mt-2 text-3 text-text-muted">
              {subtitle ?? (me ? me.display_name || me.email : 'Loading your account…')}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link to="/parent" className="text-2 text-primary underline underline-offset-4">
              Home
            </Link>
            <Link
              to="/parent/children"
              className="text-2 text-primary underline underline-offset-4"
            >
              Children
            </Link>
            <QuietButton type="button" onClick={signOut}>
              Sign out
            </QuietButton>
          </div>
        </header>

        {child ? (
          <nav aria-label={`${child.first_name}'s dashboard`} className="mt-4">
            <ul className="flex flex-wrap gap-2">
              {SECTIONS.map((section) => (
                <li key={section.label}>
                  <NavLink
                    to={`/parent/child/${child.id}/${section.to}`}
                    className={({ isActive }) =>
                      `inline-block rounded-md border px-3 py-2 text-2 ${
                        isActive
                          ? 'border-primary bg-surface font-semibold text-primary'
                          : 'border-border text-text-muted'
                      }`
                    }
                  >
                    {section.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        ) : null}

        {children}

        <footer className="mt-8 border-t border-border pt-4 text-1 text-text-muted">
          Settings and controls, the privacy centre and billing are on their way. Until then, a
          child&rsquo;s limits and topics to avoid are set when you add them, and you can delete a
          profile and everything attached to it from{' '}
          <Link to="/parent/children" className="text-primary underline underline-offset-4">
            Children
          </Link>
          .
        </footer>
      </main>
    </div>
  );
}
