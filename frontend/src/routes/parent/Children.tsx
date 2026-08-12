import { Link } from 'react-router-dom';

import { useDeleteChild, useMe } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { Card, PrimaryButton, QuietButton, Section } from './ui';

/**
 * Manage children (§12.10's last node).
 *
 * This was the whole of the parent surface on Day 2 and is now one page of it:
 * the profiles this account owns, the controls set for each, and the delete that
 * takes everything with it (§13.7). The reports, insights and transparency that
 * used to be four "coming soon" cards here are real pages now, reached from Home.
 *
 * Settings and controls proper (§12.7) arrive on Day 13, so the limits below are
 * shown as facts rather than as fields.
 */
export default function Children() {
  const { data: me, isLoading } = useMe();
  const deleteChild = useDeleteChild();
  const children = me?.children ?? [];

  return (
    <DashboardShell title="Children" subtitle="The profiles on this account.">
      <Section
        isLoading={isLoading}
        isEmpty={!isLoading && children.length === 0}
        emptyText="No child profiles yet."
      >
        <ul className="grid gap-4 sm:grid-cols-2">
          {children.map((child) => (
            <li key={child.id}>
              <Card>
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
                {/* The seeds are optional in the schema because they are optional
                    on *input*; a response always carries the list. */}
                {(child.avoid_topics ?? []).length > 0 ? (
                  <p className="mt-2 text-1 text-text-muted">
                    Never: {(child.avoid_topics ?? []).join(', ')}
                  </p>
                ) : null}
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <Link
                    to={`/parent/child/${child.id}/reports`}
                    className="text-2 text-primary underline underline-offset-4"
                  >
                    Reports
                  </Link>
                  <QuietButton
                    type="button"
                    onClick={() => deleteChild.mutate(child.id)}
                    disabled={deleteChild.isPending}
                  >
                    Delete profile and all data
                  </QuietButton>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      </Section>

      <div className="mt-6 flex items-center gap-4">
        <Link to="/parent/onboarding">
          <PrimaryButton type="button">Add a child</PrimaryButton>
        </Link>
        <Link to="/" className="text-2 text-primary underline underline-offset-4">
          Back to the story
        </Link>
      </div>
    </DashboardShell>
  );
}
