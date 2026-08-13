/**
 * The last error path in the child UI (PRODUCT.md §13, §4.9, §14.5, Day 15).
 *
 * Every *expected* failure on the child surface was already handled: a dead
 * stream leaves the previous scene up, a failed POST reopens and replays, a
 * missing illustration renders the companion thinking, a provider outage becomes
 * a pre-vetted warm beat before the wire is even reached. `StoryPage` has no
 * error state and says so at the top of the file, which is right.
 *
 * What had no handler was the **unexpected** one: a render-time exception. A bad
 * field on a beat, a `null` where a shape was assumed, a browser API missing on
 * somebody's tablet — React unmounts the whole tree, and React Router's default
 * error element renders "Unexpected Application Error!" and a stack trace. To a
 * four-year-old holding the tablet, that is a white screen with red writing on
 * it, which is precisely the thing §13 forbids and the one path that could still
 * produce it.
 *
 * **What this renders instead is not an error screen.** It is the companion,
 * resting — the same component, the same character, the same posture the child
 * already knows from the wellbeing window (§13.6) and from every gap while a beat
 * generates. There is no apology, no "something went wrong", no retry button
 * shaped like a demand, and nothing that reads as the child having broken
 * something. One large soft control offers to go back to the beginning, which is
 * the only recovery a child could ever perform, and the story they were in is
 * still `in_progress` on the server so it is waiting for them (§14.5).
 *
 * **The grown-up is told; the child is not.** `onError` logs the real exception
 * to the console and calls the reporter, because a degradation nobody can see is
 * one that runs for a week. What reaches the screen carries none of it.
 *
 * The parent tree gets a different boundary (`ParentBoundary` below): a parent
 * *can* act on "this page did not load", and hiding it from them would waste
 * their time and their trust. Two audiences, two honesties.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
  /** Told about every caught error. The console is the default. */
  onError?: (error: Error, info: ErrorInfo) => void;
};

type State = { failed: boolean };

class Boundary extends Component<Props & { render: (reset: () => void) => ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Never swallowed. The child sees nothing; somebody has to see everything.
    console.error('[primer] render failed', error, info.componentStack);
    this.props.onError?.(error, info);
  }

  reset = () => this.setState({ failed: false });

  render() {
    return this.state.failed ? this.props.render(this.reset) : this.props.children;
  }
}

/**
 * The child tree's boundary. A resting companion, and a way back.
 *
 * `window.location.assign('/')` rather than a router navigation, deliberately:
 * the tree that just threw is the tree a client-side navigation would keep
 * mounted, so the one recovery worth offering is the one that starts the app
 * over. It is also what a child's parent would do anyway.
 */
export function ChildBoundary({ children, onError }: Props) {
  return (
    <Boundary
      onError={onError}
      render={() => (
        <main
          data-testid="child-safe-boundary"
          className="min-h-full bg-bg text-text flex flex-col items-center justify-center p-6 text-center"
        >
          <div className="w-full max-w-[36rem]">
            <RestingCompanion />
            {/* A line of story, not a status. It says the same thing the
                wellbeing window says, because from the child's side it is the
                same thing: the friend is having a rest and will be back. */}
            <p className="font-display text-4 mt-6 leading-[1.45] text-text">
              Your friend is having a little rest. Shall we go back to the beginning?
            </p>
            <button
              type="button"
              onClick={() => window.location.assign('/')}
              data-testid="child-safe-boundary-home"
              aria-label="Go back to the beginning"
              className="mt-8 min-h-target rounded-full bg-accent px-10 text-5 font-display text-surface shadow-card transition-transform duration-base motion-safe:hover:scale-105 active:scale-95"
            >
              Back to the start
            </button>
          </div>
        </main>
      )}
    >
      {children}
    </Boundary>
  );
}

/**
 * The parent tree's boundary.
 *
 * A parent gets the truth and a control that actually retries in place, because
 * a parent can act on it — and because a dashboard that pretended everything was
 * fine would be a worse breach of §12's transparency than any error message.
 * Still no stack trace: that goes to the console, where it is useful, not onto a
 * page where it is alarming.
 */
export function ParentBoundary({ children, onError }: Props) {
  return (
    <Boundary
      onError={onError}
      render={(reset) => (
        <div
          role="alert"
          data-testid="parent-safe-boundary"
          className="mx-auto max-w-[36rem] p-6 text-center"
        >
          <h1 className="text-3 font-semibold text-text">This page did not load</h1>
          <p className="mt-3 text-1 text-text-muted">
            Nothing has been lost — your child&rsquo;s stories and settings are unchanged. Try
            again, and if it keeps happening, let us know.
          </p>
          <button
            type="button"
            onClick={reset}
            className="mt-6 min-h-target rounded-full bg-primary px-8 text-1 font-medium text-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          >
            Try again
          </button>
        </div>
      )}
    >
      {children}
    </Boundary>
  );
}

/**
 * The companion, resting. A deliberate near-copy of `StoryPage`'s
 * `ThinkingCompanion` in its resting state, and *not* an import of it: this
 * component has to render when the module that owns that one may be the thing
 * that just threw. A boundary that depends on the tree it is catching is not a
 * boundary.
 *
 * No portrait, because fetching one needs the query client that may also be
 * broken. The soft shape is what a child sees before their companion's art has
 * ever rendered, so it is a face they have already met.
 */
function RestingCompanion() {
  return (
    <div className="flex flex-col items-center">
      <svg
        viewBox="0 0 200 160"
        className="w-[min(46vw,15rem)]"
        role="img"
        aria-label="Your friend is having a rest"
      >
        <ellipse cx="100" cy="142" rx="66" ry="11" fill="var(--border)" opacity="0.6" />
        <g className="motion-safe:animate-[breathe_4s_ease-in-out_infinite]">
          <ellipse cx="100" cy="98" rx="60" ry="44" fill="var(--primary)" opacity="0.85" />
          <path d="M76 90 q6 5 12 0" stroke="var(--surface)" strokeWidth="3" fill="none" />
          <path d="M112 90 q6 5 12 0" stroke="var(--surface)" strokeWidth="3" fill="none" />
        </g>
      </svg>
    </div>
  );
}
