import { useParams } from 'react-router-dom';

import { useWeeklyReport } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { useDashboardChild } from './dashboard';
import { Card, Section, StatusPill } from './ui';

/**
 * The week, and the growth in it (§12.2, §12.5, §18.7).
 *
 * Four things, in the order a parent cares about them: the week's one real
 * moment, a milestone if one was genuinely reached, what was practised and how it
 * moved, and one low-pressure suggestion.
 *
 * **`completionRate` is rendered as a count, not a percentage.** "4 of 5 played to
 * the end" is the same fact as "80%" and is not a mark: a percentage on a parent
 * dashboard reads as a score even when it is measuring whether a four-year-old
 * reached the last page, and §12.3's whole point is that this surface never hands
 * a parent something to feel competitive about.
 */
export default function WeeklyReport() {
  const { childId } = useParams();
  const child = useDashboardChild(childId);
  const { data: week, isLoading, error } = useWeeklyReport(childId);

  const played = week?.sessions ?? 0;
  const finished = Math.round((week?.completionRate ?? 0) * played);

  return (
    <DashboardShell
      child={child ?? null}
      title={child ? `${child.first_name}'s week` : 'This week'}
      subtitle={week ? humanWeek(week.week) : undefined}
    >
      <Section
        isLoading={isLoading || child === undefined}
        error={error}
        isEmpty={Boolean(week) && played === 0}
        emptyText="No stories this week. Nothing to report, and nothing to worry about."
      >
        {week && played > 0 ? (
          <div className="grid gap-4">
            <Card>
              <h2 className="text-3 font-semibold">This week</h2>
              <p className="mt-2 text-2">
                {played} {played === 1 ? 'story' : 'stories'} · {finished} played to the end
              </p>
              {week.highlight ? <p className="mt-3 text-4">{week.highlight}</p> : null}
            </Card>

            {week.milestone ? (
              <Card>
                <h2 className="text-3 font-semibold">A step reached</h2>
                <p className="mt-2 text-4">{week.milestone.name}</p>
                <p className="mt-2 text-2 text-text-muted">
                  {child?.first_name ?? 'They'} can now {week.milestone.step}.
                </p>
              </Card>
            ) : null}

            <Card>
              <h2 className="text-3 font-semibold">What they practised</h2>
              {week.skills.length === 0 ? (
                <p className="mt-2 text-2 text-text-muted">
                  Nothing came up this week — the stories went other places.
                </p>
              ) : (
                <ul className="mt-3 grid gap-3">
                  {week.skills.map((skill) => (
                    <li
                      key={skill.objective}
                      className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3"
                    >
                      <span className="text-2">
                        {skill.milestone} — {skill.movement}
                      </span>
                      <StatusPill status={skill.status} />
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <h2 className="text-3 font-semibold">What they love right now</h2>
              <p className="mt-2 text-2">{week.interests_trend.join(' · ')}</p>
              {week.recommendation ? (
                <p className="mt-4 text-2 text-text-muted">{week.recommendation}</p>
              ) : null}
              <p className="mt-3 text-1 text-text-muted">
                Nothing changes unless you say so. Controls arrive with Settings.
              </p>
            </Card>
          </div>
        ) : null}
      </Section>
    </DashboardShell>
  );
}

/** `"2026-08-03/2026-08-09"` → `"3–9 Aug"`. */
function humanWeek(span: string): string {
  const [from, to] = span.split('/');
  const start = new Date(from);
  const end = new Date(to);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return span;
  const month = end.toLocaleDateString(undefined, { month: 'short' });
  return `${start.getDate()}–${end.getDate()} ${month}`;
}
