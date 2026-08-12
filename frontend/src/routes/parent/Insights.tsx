import { useParams } from 'react-router-dom';

import type { Milestone } from '@/lib/api/client';
import { useInsights } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { useDashboardChild } from './dashboard';
import { Card, Section, StatusPill } from './ui';

/**
 * Learning insights (§12.3, §12.5, §11.7).
 *
 * Per domain, grouped under the milestones of §10.6, in plain language.
 *
 * **There is no number anywhere on this page, and that is the design.** The API
 * carries `mastery` and it is never rendered — see `apps/analytics/skills.py` for
 * why a percentage here would be actively misleading rather than merely
 * unfriendly: mastery is on the *difficulty* scale, and its ceiling differs per
 * objective, so a child who has finished counting to three sits at 0.64 forever.
 * "Does this reliably" is the true statement; "64%" is not.
 *
 * No comparison to other children exists in the data and none is invented here
 * (§12.3). Nothing on this page can be read as a rank.
 */
const DOMAIN_LABEL: Record<string, string> = {
  numeracy: 'Numbers and counting',
  vocabulary: 'Words and language',
};

export default function Insights() {
  const { childId } = useParams();
  const child = useDashboardChild(childId);
  const { data, isLoading, error } = useInsights(childId);

  const byDomain = new Map<string, Milestone[]>();
  for (const milestone of data?.milestones ?? []) {
    byDomain.set(milestone.domain, [...(byDomain.get(milestone.domain) ?? []), milestone]);
  }

  return (
    <DashboardShell
      child={child ?? null}
      title={child ? `How ${child.first_name} is growing` : 'Insights'}
      subtitle="What they are working on, in plain language. Never a grade."
    >
      <Section
        isLoading={isLoading || child === undefined}
        error={error}
        isEmpty={Boolean(data) && (data?.milestones.length ?? 0) === 0}
        emptyText="Nothing to show yet. Milestones appear here once your child has played a story or two."
      >
        {data ? (
          <div className="grid gap-6">
            {[...byDomain.entries()].map(([domain, milestones]) => (
              <div key={domain}>
                <h2 className="text-4 font-semibold">{DOMAIN_LABEL[domain] ?? domain}</h2>
                <ul className="mt-3 grid gap-4">
                  {milestones.map((milestone) => (
                    <li key={milestone.name}>
                      <Card>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <h3 className="text-3 font-semibold">{milestone.name}</h3>
                          <StatusPill status={milestone.status} />
                        </div>
                        <p className="mt-2 text-2 text-text-muted">{milestone.description}</p>
                        {milestone.furthest ? (
                          <p className="mt-2 text-2">
                            Right now, {child?.first_name ?? 'they'} {milestone.furthest}.
                          </p>
                        ) : null}
                        <ul className="mt-4 grid gap-3">
                          {milestone.skills.map((skill) => (
                            <li
                              key={skill.objective}
                              className="flex flex-wrap items-start justify-between gap-2 border-t border-border pt-3"
                            >
                              <span className="max-w-[34rem] text-2">{skill.summary}</span>
                              <StatusPill status={skill.status} />
                            </li>
                          ))}
                        </ul>
                      </Card>
                    </li>
                  ))}
                </ul>
              </div>
            ))}

            <Card>
              <h2 className="text-3 font-semibold">What they love</h2>
              <p className="mt-2 text-2">
                {data.interests.now.length > 0
                  ? data.interests.now.join(' · ')
                  : 'Not enough play yet to say.'}
              </p>
              {data.interests.story ? (
                <p className="mt-3 text-2 text-text-muted">{data.interests.story}</p>
              ) : null}
              <p className="mt-3 text-1 text-text-muted">
                Read from what they choose and say in stories, not from anything they were asked.
              </p>
            </Card>

            <p className="text-1 text-text-muted">
              None of this is a score, and none of it compares your child to anyone else. There is
              no test in The Primer — everything here is read from how they played.
            </p>
          </div>
        ) : null}
      </Section>
    </DashboardShell>
  );
}
