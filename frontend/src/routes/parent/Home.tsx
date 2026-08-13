import { Link } from 'react-router-dom';

import type { Child } from '@/lib/api/client';
import { useMe, useSessionReports } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { ENDING, when } from './dashboard';
import { Card, LINK, LinkButton, Section } from './ui';

/**
 * Parent Home — today's highlight and the way into everything else (§12.10).
 *
 * One card per child, and each card leads with the **most recent real moment**
 * rather than with a count of sessions. That is §12.1's argument applied to the
 * first screen: a parent who opens this and reads something true and specific
 * about their own child renews; a parent who reads "3 sessions this week" has
 * been shown a dashboard.
 *
 * A child who has not played yet says so plainly. It would be easy to fill the
 * space with an encouraging sentence, and that is the exact habit §12.1 forbids —
 * the space is empty because nothing has happened.
 *
 * **The one empty state that gets more than a sentence is the first-run one.**
 * "No child profiles yet." is true, and on the very first visit it is also a dead
 * end: a parent who skipped the child step during onboarding lands here and is
 * told what is missing with no way to fix it. That is a different situation from
 * a report that has not generated — nothing has gone wrong, the product simply
 * has not started — so it gets a sentence about what happens next and the button
 * that starts it. Every other empty state on this surface stays one line.
 */
export default function Home() {
  const { data: me, isLoading } = useMe();
  const children = me?.children ?? [];
  const firstRun = !isLoading && children.length === 0;

  return (
    <DashboardShell title="Parent area">
      {firstRun ? (
        <Section title="Your children">
          <Card>
            <h3 className="text-4 font-semibold">Nobody here yet</h3>
            <p className="mt-2 text-2 text-text-muted">
              Add a child and pick their companion — a name, a birthday and two minutes. Everything
              else is optional, and you can change all of it later.
            </p>
            <p className="mt-4">
              <LinkButton to="/parent/onboarding">Add your first child</LinkButton>
            </p>
          </Card>
        </Section>
      ) : (
        <Section title="Your children" isLoading={isLoading}>
          <ul className="grid gap-4">
            {children.map((child) => (
              <li key={child.id}>
                <ChildCard child={child} />
              </li>
            ))}
          </ul>
        </Section>
      )}
    </DashboardShell>
  );
}

function ChildCard({ child }: { child: Child }) {
  const { data: reports, isLoading, error } = useSessionReports(child.id);
  const latest = reports?.[0];

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-4 font-semibold">{child.first_name}</h3>
        <p className="text-1 text-text-muted">
          Age {child.age} · intensity {child.intensity_ceiling}/3 · {child.session_cap_min} min
          {(child.avoid_topics ?? []).length > 0
            ? ` · never: ${(child.avoid_topics ?? []).join(', ')}`
            : ''}
        </p>
      </div>

      {isLoading ? <p className="mt-3 text-2 text-text-muted">Loading…</p> : null}
      {error ? (
        <p className="mt-3 text-2 text-text-muted" role="alert">
          We could not load {child.first_name}&rsquo;s reports just now.
        </p>
      ) : null}

      {!isLoading && !error && !latest ? (
        <p className="mt-3 text-2 text-text-muted">
          No stories yet. The first report appears here once {child.first_name} has played.
        </p>
      ) : null}

      {latest ? (
        <div className="mt-3">
          <p className="text-3">{latest.highlight}</p>
          <p className="mt-2 text-1 text-text-muted">
            {when(latest.occurred_at)} · {latest.adventure_title || 'An adventure'}
            {latest.world_name ? ` · ${latest.world_name}` : ''} ·{' '}
            {ENDING[latest.ended_reason] ?? latest.ended_reason}
          </p>
          <p className="mt-3">
            <Link
              to={`/parent/child/${child.id}/reports/${latest.session_id}`}
              className={`text-2 ${LINK}`}
            >
              Read the full report
            </Link>
          </p>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-3 text-2">
        <Link to={`/parent/child/${child.id}`} className={LINK}>
          {child.first_name}&rsquo;s dashboard
        </Link>
        <Link to={`/parent/child/${child.id}/transparency`} className={LINK}>
          Read their stories
        </Link>
      </div>
    </Card>
  );
}
