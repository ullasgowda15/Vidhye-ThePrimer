import { Link, useNavigate } from 'react-router-dom';

import { useDeleteChild, useMe } from '@/lib/api/queries';
import { useAuthStore } from '@/store/auth';

import { PrimaryButton, QuietButton } from './ui';

/**
 * The parent surface — Calm theme (PRODUCT.md §15.1).
 *
 * The `data-theme="calm"` wrapper is the whole switch: every token underneath
 * re-resolves, so the same primitives read as a quiet dashboard here and as a
 * storybook on the child route. No component branches on which surface it is
 * rendering into.
 *
 * Day 2 shows the children this account owns and the controls set for each.
 * Reports, insights and billing arrive on Days 12-14.
 */
export default function ParentHome() {
  const navigate = useNavigate();
  const { data: me, isLoading } = useMe();
  const deleteChild = useDeleteChild();
  const logout = useAuthStore((state) => state.logout);

  const children = me?.children ?? [];

  async function signOut() {
    await logout();
    navigate('/', { replace: true });
  }

  return (
    <div data-theme="calm" className="min-h-full bg-bg text-text">
      <main className="mx-auto max-w-[52rem] p-6">
        <header className="flex items-start justify-between border-b border-border pb-4">
          <div>
            <h1 className="font-text text-5 font-semibold">Parent area</h1>
            <p className="mt-2 text-3 text-text-muted">
              {me ? me.display_name || me.email : 'Loading your account…'}
            </p>
          </div>
          <QuietButton type="button" onClick={signOut}>
            Sign out
          </QuietButton>
        </header>

        <section className="mt-5">
          <h2 className="text-4 font-semibold">Children</h2>

          {!isLoading && children.length === 0 ? (
            <div className="mt-4 rounded-lg border border-border bg-surface p-5">
              <p className="text-2 text-text-muted">No child profiles yet.</p>
              <p className="mt-3">
                <Link to="/parent/onboarding" className="text-primary underline underline-offset-4">
                  Add your first child
                </Link>
              </p>
            </div>
          ) : null}

          <ul className="mt-4 grid gap-4 sm:grid-cols-2">
            {children.map((child) => (
              <li
                key={child.id}
                className="rounded-lg border border-border bg-surface p-5 shadow-card"
              >
                <h3 className="text-3 font-semibold">{child.first_name}</h3>
                <p className="mt-1 text-2 text-text-muted">
                  Age {child.age} · intensity {child.intensity_ceiling}/3 · {child.session_cap_min}{' '}
                  min
                </p>
                {child.age_out_of_range ? (
                  <p className="mt-2 text-1 text-text-muted">
                    The Primer is designed for ages 3-7; stories will still be told.
                  </p>
                ) : null}
                {/* The seeds are optional in the schema because they are
                    optional on *input*; a response always carries the list. */}
                {(child.avoid_topics ?? []).length > 0 ? (
                  <p className="mt-2 text-1 text-text-muted">
                    Never: {(child.avoid_topics ?? []).join(', ')}
                  </p>
                ) : null}
                <div className="mt-4">
                  <QuietButton
                    type="button"
                    onClick={() => deleteChild.mutate(child.id)}
                    disabled={deleteChild.isPending}
                  >
                    Delete profile and all data
                  </QuietButton>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-6 grid gap-4 sm:grid-cols-2">
          {[
            { title: 'Today', body: 'Session summaries appear here after the first story.' },
            { title: 'Learning', body: 'Objectives practised and mastery over time.' },
            { title: 'Growth', body: 'Milestones and the memories behind them.' },
            { title: 'Controls', body: 'Time limits, intensity ceiling, topics to avoid.' },
          ].map((card) => (
            <article
              key={card.title}
              className="rounded-lg border border-border bg-surface p-5 shadow-card"
            >
              <h2 className="text-3 font-semibold">{card.title}</h2>
              <p className="mt-2 text-2 text-text-muted">{card.body}</p>
            </article>
          ))}
        </section>

        <div className="mt-6 flex items-center gap-4">
          <Link to="/parent/onboarding">
            <PrimaryButton type="button">Add a child</PrimaryButton>
          </Link>
          <Link to="/" className="text-2 text-primary underline underline-offset-4">
            Back to the story
          </Link>
        </div>
      </main>
    </div>
  );
}
