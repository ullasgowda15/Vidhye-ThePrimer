import { Link } from 'react-router-dom';

/**
 * The parent surface — Calm theme (PRODUCT.md §15.1).
 *
 * The `data-theme="calm"` wrapper is the whole switch: every token underneath
 * re-resolves, so the same primitives read as a quiet dashboard here and as a
 * storybook on the child route. No component branches on which surface it is
 * rendering into.
 *
 * Placeholder for Day 1 — reports, controls, and billing arrive on Day 12-14.
 */
export default function ParentHome() {
  return (
    <div data-theme="calm" className="min-h-full bg-bg text-text">
      <main className="mx-auto max-w-[52rem] p-6">
        <header className="border-b border-border pb-4">
          <h1 className="font-text text-5 font-semibold">Parent area</h1>
          <p className="mt-2 text-3 text-text-muted">
            Reports, learning insights, and controls will live here.
          </p>
        </header>

        <section className="mt-5 grid gap-4 sm:grid-cols-2">
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

        <p className="mt-6 text-2">
          <Link to="/" className="text-primary underline underline-offset-4">
            Back to the story
          </Link>
        </p>
      </main>
    </div>
  );
}
