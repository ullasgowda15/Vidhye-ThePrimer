import { Link, useNavigate } from 'react-router-dom';

import { useHealth, useMe } from '@/lib/api/queries';
import Landing from '@/routes/Landing';
import { useAuthStore } from '@/store/auth';

/**
 * The child landing surface — Storyland theme (PRODUCT.md §15.1).
 *
 * The star begins today's adventure. It can do that because the parent session
 * now survives a cold start (the httpOnly refresh cookie, §18.2): the child taps,
 * and the request goes out under their parent's session without anybody having to
 * log in — which is the only version of this that works, since a three-year-old
 * cannot pass a login form (§14.5).
 *
 * From Day 9 the star goes to `/welcome/:childId` rather than straight to the
 * story, and that one hop is §4.5: the companion says hello and says what it
 * remembers before today's adventure starts. It is not a detour the child can land
 * on wrongly — a child with no history is greeted with the first-meeting hello
 * there, which is the scene they would otherwise have seen at `/meet`.
 *
 * **Two audiences arrive at `/`, and they are asking different questions.** A
 * child holding a tablet their parent set up wants the star. A stranger wants to
 * know what this is. They used to get the same page — a storybook inviting them
 * to tap a star, with no answer to "should I trust this with my four-year-old?" —
 * so a visitor with no child profile now gets `Landing` instead. A family never
 * sees it, and a child is never handed marketing.
 *
 * **Siblings each get their own star.** This used to open `children[0]` and
 * nothing else, so on a two-child account the younger one could not reach their
 * own story from the front door at all — their parent's dashboard link went to
 * the same place. With more than one profile the star becomes one star per child,
 * named, which is also the only version a pre-reader can use: they cannot read a
 * dropdown, but they know their own name when a grown-up reads it once.
 *
 * Note what is deliberately absent. No text the child must read to navigate, no
 * error state, no spinner. If there is no session yet, the star simply stays a
 * star — a four-year-old must never be shown a system failure (§14.5), and
 * "nothing happened" is a far better answer than an explanation they cannot read.
 */
export default function ChildHome() {
  const navigate = useNavigate();
  // Only `data` is read. Whether the health check *failed* or is merely slow is
  // a distinction this screen deliberately does not draw — see the footer.
  const { data } = useHealth();
  const { data: me, isLoading } = useMe();
  const authenticated = useAuthStore((state) => state.accessToken !== null);
  const ok = data?.status === 'ok';

  // **Read the session, not just the cache.** `useMe` is disabled when there is
  // no token, and a disabled query still hands back whatever it fetched last —
  // so after signing out this page went on rendering a star per child, with
  // their first names on it, for whoever picked the tablet up next. `useLogout`
  // now clears the cache, and this is the second lock on the same door: a child's
  // name is not something to leave resting on one of them.
  const children = authenticated ? (me?.children ?? []) : [];

  // Nobody signed in, or signed in with no child yet: this visitor is a grown-up
  // deciding, not a child playing. `isLoading` is held back rather than treated
  // as "no children", so a family on a slow connection never flashes a sales page
  // at a child on the way to their story.
  if (!authenticated || (!isLoading && children.length === 0)) {
    return <Landing />;
  }

  const only = children.length === 1 ? children[0] : null;

  return (
    <main className="min-h-full bg-bg text-text flex flex-col items-center justify-center p-6 text-center">
      <div className="max-w-[36rem] rounded-lg bg-surface shadow-card px-7 py-8">
        <p className="font-text text-2 uppercase tracking-[0.2em] text-text-muted">
          Once upon a time
        </p>
        <h1 className="font-display text-7 leading-[1.1] mt-4 text-primary">The Primer</h1>
        <p className="font-display text-4 mt-5 text-text">
          {children.length > 1
            ? 'Whose story is it tonight?'
            : "Your story is waking up. Tap the star when you're ready to begin."}
        </p>

        {/* Sized to the child target minimum from the start — retrofitting
            touch targets later is how you end up with a frustrated toddler. */}
        {only ? (
          <button
            type="button"
            onClick={() => navigate(`/welcome/${only.id}`)}
            className="mt-6 min-h-target min-w-target rounded-full bg-accent px-7 text-5 font-display text-surface shadow-card transition-transform duration-base motion-safe:hover:scale-[1.03] motion-safe:active:scale-95"
            aria-label="Begin the story"
            data-testid="begin-story"
          >
            ★
          </button>
        ) : (
          <ul className="mt-6 flex flex-wrap items-center justify-center gap-5">
            {children.map((child) => (
              <li key={child.id}>
                <button
                  type="button"
                  onClick={() => navigate(`/welcome/${child.id}`)}
                  className="flex min-h-target min-w-target flex-col items-center gap-2 rounded-lg px-4 py-3 transition-transform duration-base motion-safe:hover:scale-[1.03] motion-safe:active:scale-95"
                  aria-label={`Begin ${child.first_name}'s story`}
                  data-testid={`begin-story-${child.id}`}
                >
                  <span
                    aria-hidden="true"
                    className="flex h-16 w-16 items-center justify-center rounded-full bg-accent text-5 font-display text-surface shadow-card"
                  >
                    ★
                  </span>
                  {/* Read aloud by whoever hands the tablet over. A pre-reader
                      cannot read their name, but they recognise it being said. */}
                  <span className="font-display text-3 text-text">{child.first_name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <footer className="mt-6 flex items-center gap-2 text-1 text-text-muted">
        {/* Colour is never the sole signal (§15.1) — the label carries it too. */}
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 rounded-full"
          style={{ background: ok ? 'var(--success)' : 'var(--accent)' }}
        />
        {/* Two words from the story world, never from the network stack (§14.5,
            and the Day 1–15 audit's F-9). This used to render `offline` when the
            health check failed — three lines below a docstring promising no error
            state — and the objection is not that a pre-reader would read it. It is
            that a system failure had been given a place on the child's own screen,
            and the next person debugging a connectivity problem makes that place
            bigger. `isError` and "still loading" now say the same thing on
            purpose: the difference between them is a grown-up's question, and the
            grown-ups' door is the link beside this one. */}
        <span data-testid="health-status">
          {ok ? 'the story is awake' : 'the story is dreaming'}
        </span>
        <Link to="/parent" className="ml-4 underline underline-offset-4">
          Grown-ups
        </Link>
      </footer>
    </main>
  );
}
