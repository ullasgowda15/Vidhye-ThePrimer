import { Link, useParams } from 'react-router-dom';

import { useSessionReport } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { ENDING, useDashboardChild, when } from './dashboard';
import { Card, Section, StatusPill } from './ui';

/**
 * One sitting, in full (§12.1).
 *
 * The order on the page is §12.1's own order — where they went, the choice that
 * mattered, what was quietly practised and how it went, and the one real moment —
 * with the moment pulled to the top because it is the reason a parent opened this.
 *
 * **The provenance line is deliberate.** `highlight_source` says whether the
 * sentence was written by a model (and checked against the session), assembled
 * from what the child did, or is us saying the afternoon was quiet. §13.9 makes
 * fabrication the named risk of this surface, and a product that answers it by
 * promising to be careful is asking for the trust §12.6 says we have to earn.
 * Saying which kind of sentence this is costs one small line and is checkable.
 */
const PROVENANCE: Record<string, string> = {
  model: 'Written from this session by our AI, and checked against what actually happened.',
  data: 'Assembled from exactly what your child said and chose.',
  quiet: 'Nothing stood out today, and we would rather say so than make something up.',
};

export default function SessionReport() {
  const { childId, sessionId } = useParams();
  const child = useDashboardChild(childId);
  const { data: report, isLoading, error } = useSessionReport(childId, sessionId);

  return (
    <DashboardShell
      child={child ?? null}
      title={child ? `${child.first_name}'s session` : 'Session report'}
      subtitle={report ? when(report.occurred_at) : undefined}
    >
      <Section isLoading={isLoading || child === undefined} error={error} isEmpty={!report}>
        {report ? (
          <div className="grid gap-4">
            <Card>
              <h2 className="text-3 font-semibold">The moment</h2>
              <p className="mt-2 text-4">{report.highlight}</p>
              <p className="mt-3 text-1 text-text-muted">
                {PROVENANCE[report.highlight_source] ?? report.highlight_source}
              </p>
            </Card>

            <Card>
              <h2 className="text-3 font-semibold">Where they went</h2>
              <p className="mt-2 text-2">
                {report.adventure_title || 'An adventure'}
                {report.world_name ? ` — ${report.world_name}` : ''}
                {report.place_name ? `, at ${report.place_name}` : ''}
              </p>
              <p className="mt-2 text-1 text-text-muted">
                {report.beats} {report.beats === 1 ? 'scene' : 'scenes'} ·{' '}
                {Math.max(1, Math.round(report.duration_s / 60))} min ·{' '}
                {ENDING[report.ended_reason] ?? report.ended_reason}
              </p>
              {report.adventure_id ? (
                <p className="mt-3">
                  <Link
                    to={`/parent/child/${childId}/transparency/${report.adventure_id}`}
                    className="text-2 text-primary underline underline-offset-4"
                  >
                    Read the story itself, word for word
                  </Link>
                </p>
              ) : null}
            </Card>

            {report.key_choice ? (
              <Card>
                <h2 className="text-3 font-semibold">The choice that mattered</h2>
                <p className="mt-2 text-2">{report.key_choice}</p>
              </Card>
            ) : null}

            {report.milestone_name ? (
              <Card>
                <h2 className="text-3 font-semibold">What the story was quietly about</h2>
                <p className="mt-2 text-2">{report.milestone_name}</p>
                {report.how_it_went ? (
                  <p className="mt-2 text-2 text-text-muted">{report.how_it_went}</p>
                ) : null}
                {report.skills.length > 0 ? (
                  <ul className="mt-4 grid gap-3">
                    {report.skills.map((skill) => (
                      <li
                        key={skill.code}
                        className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3"
                      >
                        <span className="text-2">
                          {skill.milestone} · asked {skill.asks}{' '}
                          {skill.asks === 1 ? 'time' : 'times'}
                          {skill.unaided > 0 ? `, ${skill.unaided} on their own` : ''}
                          {skill.assisted > 0 ? `, ${skill.assisted} together` : ''}
                        </span>
                        <StatusPill status={skill.status} />
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className="mt-4 text-1 text-text-muted">
                  Your child was never told any of this. There is no test in The Primer — we read
                  how a story went from how they played it.
                </p>
              </Card>
            ) : null}
          </div>
        ) : null}
      </Section>
    </DashboardShell>
  );
}
