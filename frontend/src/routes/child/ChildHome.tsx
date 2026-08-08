import { Link, useNavigate } from 'react-router-dom';

import { useHealth, useMe } from '@/lib/api/queries';

/**
 * The child landing surface — Storyland theme (PRODUCT.md §15.1).
 *
 * The star begins today's adventure. It can do that because the parent session
 * now survives a cold start (the httpOnly refresh cookie, §18.2): the child taps,
 * and the request goes out under their parent's session without anybody having to
 * log in — which is the only version of this that works, since a three-year-old
 * cannot pass a login form (§14.5).
 *
 * One child per account at MVP, so the first child on `/me` is *the* child. A
 * picker belongs here when siblings do.
 *
 * Note what is deliberately absent. No text the child must read to navigate, no
 * error state, no spinner. If there is no session or no child yet, the star simply
 * stays a star — a four-year-old must never be shown a system failure (§14.5), and
 * "nothing happened" is a far better answer than an explanation they cannot read.
 */
export default function ChildHome() {
  const navigate = useNavigate();
  const { data, isError } = useHealth();
  const { data: me } = useMe();
  const ok = data?.status === 'ok';

  const child = me?.children?.[0];

  return (
    <main className="min-h-full bg-bg text-text flex flex-col items-center justify-center p-6 text-center">
      <div className="max-w-[36rem] rounded-lg bg-surface shadow-card px-7 py-8">
        <p className="font-text text-2 uppercase tracking-[0.2em] text-text-muted">
          Once upon a time
        </p>
        <h1 className="font-display text-7 leading-[1.1] mt-4 text-primary">The Primer</h1>
        <p className="font-display text-4 mt-5 text-text">
          Your story is waking up. Tap the star when you&apos;re ready to begin.
        </p>

        {/* Sized to the child target minimum from the start — retrofitting
            touch targets later is how you end up with a frustrated toddler. */}
        <button
          type="button"
          onClick={() => {
            if (child) navigate(`/story/${child.id}`);
          }}
          className="mt-6 min-h-target min-w-target rounded-full bg-accent px-7 text-5 font-display text-surface shadow-card transition-transform duration-base hover:scale-[1.03] active:scale-95"
          aria-label="Begin the story"
          data-testid="begin-story"
        >
          ★
        </button>
      </div>

      <footer className="mt-6 flex items-center gap-2 text-1 text-text-muted">
        {/* Colour is never the sole signal (§15.1) — the label carries it too. */}
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 rounded-full"
          style={{ background: ok ? 'var(--success)' : 'var(--accent)' }}
        />
        <span data-testid="health-status">{ok ? 'ok' : isError ? 'offline' : 'waking up'}</span>
        <Link to="/parent" className="ml-4 underline underline-offset-4">
          Grown-ups
        </Link>
      </footer>
    </main>
  );
}
