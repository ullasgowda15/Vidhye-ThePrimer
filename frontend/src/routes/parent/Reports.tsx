import { Link, useParams } from 'react-router-dom';

import { useSessionReports } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { ENDING, useDashboardChild, when } from './dashboard';
import { Card, Section } from './ui';

/**
 * Every sitting this child has had, newest first (§12.10's Session Reports).
 *
 * Each row leads with the highlight, for the same reason Home does: the date and
 * the world are how a parent finds the afternoon they are looking for, and the
 * sentence is why they came.
 */
export default function Reports() {
  const { childId } = useParams();
  const child = useDashboardChild(childId);
  const { data, isLoading, error } = useSessionReports(childId);

  return (
    <DashboardShell
      child={child ?? null}
      title={child ? `${child.first_name}'s reports` : 'Reports'}
      subtitle="One short, honest recap per sitting."
    >
      <Section
        isLoading={isLoading || child === undefined}
        error={error}
        isEmpty={!isLoading && (data ?? []).length === 0}
        emptyText="No stories yet. A report appears here after each sitting."
      >
        <ul className="grid gap-4">
          {(data ?? []).map((report) => (
            <li key={report.session_id}>
              <Card>
                <p className="text-1 text-text-muted">
                  {when(report.occurred_at)} · {report.adventure_title || 'An adventure'}
                  {report.world_name ? ` · ${report.world_name}` : ''} ·{' '}
                  {ENDING[report.ended_reason] ?? report.ended_reason}
                </p>
                <p className="mt-2 text-3">{report.highlight}</p>
                <p className="mt-3">
                  <Link
                    to={`/parent/child/${childId}/reports/${report.session_id}`}
                    className="text-2 text-primary underline underline-offset-4"
                  >
                    Read the full report
                  </Link>
                </p>
              </Card>
            </li>
          ))}
        </ul>
      </Section>
    </DashboardShell>
  );
}
