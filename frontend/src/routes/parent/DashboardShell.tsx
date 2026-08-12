import { Link, NavLink, useNavigate } from 'react-router-dom';

import { useMe } from '@/lib/api/queries';
import { useAuthStore } from '@/store/auth';

import type { ChildSummary } from './dashboard';
import { FOCUS_RING, LINK, QuietButton, SkipLink } from './ui';

/**
 * The parent dashboard's frame and its information architecture (§12.10, §15.1).
 *
 * `data-theme="calm"` is the whole theme switch: every token underneath
 * re-resolves, so the same primitives read as a quiet dashboard here and as a
 * storybook on the child route. No component branches on which surface it is in.
 *
 * §12.10's map is Home → Reports / Weekly-Growth / Insights / Transparency /
 * Settings / Privacy / Billing / Children. All but Billing are built. Billing is
 * named in a plain line at the foot rather than rendered as a nav item that goes
 * nowhere — a dead link in a product whose headline feature is honesty is a small
 * lie in the wrong place. *(This used to say "it arrives on Day 14". It did not:
 * Day 14 is analytics, cost, accessibility and polish, and its brief rules
 * billing out explicitly. The line at the foot says "on its way" and nothing more
 * specific, which is the claim we can actually keep.)*
 *
 * Privacy is in the header rather than in `SECTIONS`, because `SECTIONS` is the
 * per-child nav and the privacy centre is per-account: export and account deletion
 * span every child, and a link that sat under one child's name would say something
 * untrue about what the page does.
 *
 * **Accessibility (§14.6).** Landmarks are banner / navigation / main / content-
 * info, in that order and not nested; the skip link is the first focusable thing
 * on the page, because without it every visit tabs through eight header and nav
 * links before reaching the report the parent came for. Focus indicators and
 * target sizes come from `ui.tsx`, which owns every interactive element here.
 */
const SECTIONS = [
  { to: 'reports', label: 'Reports' },
  { to: 'weekly', label: 'Weekly & growth' },
  { to: 'insights', label: 'Insights' },
  { to: 'transparency', label: 'Transparency' },
  { to: 'settings', label: 'Settings' },
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

  const link = `text-2 ${LINK}`;

  return (
    <div data-theme="calm" className="relative min-h-full bg-bg text-text">
      <SkipLink />
      <div className="mx-auto max-w-[56rem] p-6">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
          <div>
            <h1 className="font-text text-5 font-semibold">{title}</h1>
            <p className="mt-2 text-3 text-text-muted">
              {subtitle ?? (me ? me.display_name || me.email : 'Loading your account…')}
            </p>
          </div>
          <nav aria-label="Account" className="flex flex-wrap items-center gap-3">
            <Link to="/parent" className={link}>
              Home
            </Link>
            <Link to="/parent/children" className={link}>
              Children
            </Link>
            <Link to="/parent/privacy" className={link}>
              Privacy
            </Link>
            <QuietButton type="button" onClick={signOut}>
              Sign out
            </QuietButton>
          </nav>
        </header>

        {child ? (
          <nav aria-label={`${child.first_name}'s dashboard`} className="mt-4">
            <ul className="flex flex-wrap gap-2">
              {SECTIONS.map((section) => (
                <li key={section.label}>
                  {/* `NavLink` sets `aria-current="page"` on the active entry by
                      itself, which is what stops the bold-and-bordered styling
                      from being the *only* signal that a section is the one you
                      are on (§14.6: colour is never the sole signal). */}
                  <NavLink
                    to={`/parent/child/${child.id}/${section.to}`}
                    className={({ isActive }) =>
                      `inline-flex min-h-[--target-min] items-center rounded-md border px-3 py-2 text-2 ${FOCUS_RING} ${
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

        {/* See `ParentShell`: the `tabIndex` is what makes the skip link move
            focus rather than only the scroll position. */}
        <main id="main-content" tabIndex={-1}>
          {children}
        </main>

        <footer className="mt-8 border-t border-border pt-4 text-1 text-text-muted">
          Billing is on its way. Everything else is here: limits, topics and accessibility are under
          a child&rsquo;s <strong>Settings</strong>, and your data — a full copy, or deleting it —
          is in{' '}
          <Link to="/parent/privacy" className={link}>
            Privacy
          </Link>
          .
        </footer>
      </div>
    </div>
  );
}
