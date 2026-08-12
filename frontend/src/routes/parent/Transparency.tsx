import { Link, useParams } from 'react-router-dom';

import { useMemories, useTranscript, useTranscripts } from '@/lib/api/queries';

import { DashboardShell } from './DashboardShell';
import { useDashboardChild, when } from './dashboard';
import { Card, Section } from './ui';

/**
 * The transparency log (§12.6) — the headline trust feature, not a footnote.
 *
 * Two things live here, and both answer the same parent question. The stories are
 * *exactly* what their child saw and heard, word for word, with what they tapped
 * under each scene. The memories are everything the companion has kept about
 * them, in the companion's own words.
 *
 * **Nothing on this page is summarised.** There is no "key moments" view and no
 * highlight reel, because a curated transparency log is not one. The only thing
 * the page adds to the stored beats is a label when the safety layer rewrote one —
 * showing that is uncomfortable and is the point: a product that only shows the
 * parts that went smoothly is back to asking to be trusted (§13.2).
 */
export default function Transparency() {
  const { childId, adventureId } = useParams();
  const child = useDashboardChild(childId);

  return adventureId ? (
    <OneStory childId={childId} adventureId={adventureId} child={child ?? null} />
  ) : (
    <TheLog childId={childId} child={child ?? null} />
  );
}

type Child = { id: string; first_name: string } | null;

function TheLog({ childId, child }: { childId: string | undefined; child: Child }) {
  const stories = useTranscripts(childId);
  const memories = useMemories(childId);

  return (
    <DashboardShell
      child={child}
      title={child ? `Everything ${child.first_name} has seen` : 'Transparency'}
      subtitle="Every story, word for word, and everything the companion remembers."
    >
      <Section
        title="Their stories"
        hint="Read exactly what was said to your child — nothing is summarised or left out."
        isLoading={stories.isLoading || child === undefined}
        error={stories.error}
        isEmpty={!stories.isLoading && (stories.data ?? []).length === 0}
        emptyText="No stories yet."
      >
        <ul className="grid gap-3">
          {(stories.data ?? []).map((story) => (
            <li key={story.adventureId}>
              <Card>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-3 font-semibold">{story.title}</h3>
                  <p className="text-1 text-text-muted">
                    {when(story.startedAt)} · {story.beats} {story.beats === 1 ? 'scene' : 'scenes'}
                  </p>
                </div>
                <p className="mt-3">
                  <Link
                    to={`/parent/child/${childId}/transparency/${story.adventureId}`}
                    className="text-2 text-primary underline underline-offset-4"
                  >
                    Read it
                  </Link>
                </p>
              </Card>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title="What the companion remembers"
        hint="The things it kept, in its own words. Everything else was let go."
        isLoading={memories.isLoading}
        error={memories.error}
        isEmpty={!memories.isLoading && (memories.data ?? []).length === 0}
        emptyText="Nothing kept yet. Memories are written after a sitting ends."
      >
        <ul className="grid gap-3">
          {(memories.data ?? []).map((memory) => (
            <li key={memory.id}>
              <Card>
                <p className="text-2">{memory.content}</p>
                <p className="mt-2 text-1 text-text-muted">
                  {memory.type_label} · {when(memory.created_at)}
                </p>
              </Card>
            </li>
          ))}
        </ul>
      </Section>
    </DashboardShell>
  );
}

function OneStory({
  childId,
  adventureId,
  child,
}: {
  childId: string | undefined;
  adventureId: string;
  child: Child;
}) {
  const { data: story, isLoading, error } = useTranscript(adventureId);

  return (
    <DashboardShell
      child={child}
      title={story?.title || 'The story'}
      subtitle={
        story
          ? `${when(story.startedAt)}${story.world ? ` · ${story.world}` : ''}${
              story.place ? ` · ${story.place}` : ''
            }`
          : undefined
      }
    >
      <p className="mt-4">
        <Link
          to={`/parent/child/${childId}/transparency`}
          className="text-2 text-primary underline underline-offset-4"
        >
          ← All stories
        </Link>
      </p>

      <Section isLoading={isLoading || child === undefined} error={error} isEmpty={!story}>
        <ol className="grid gap-5">
          {(story?.beats ?? []).map((beat) => (
            <li key={beat.seq}>
              <Card>
                {beat.imageUrl ? (
                  <img
                    src={beat.imageUrl}
                    alt=""
                    className="mb-4 w-full max-w-[28rem] rounded-md border border-border"
                  />
                ) : null}
                {/* `whitespace-pre-line` so the narration keeps the shape it was
                    told in. Reflowing it would be a small edit to something this
                    page promises not to edit. */}
                <p className="whitespace-pre-line text-3 leading-relaxed">{beat.narration}</p>
                {beat.asked ? (
                  <p className="mt-3 text-2 text-text-muted">
                    The companion asked: &ldquo;{beat.asked}&rdquo;
                  </p>
                ) : null}
                {beat.options.length > 0 ? (
                  <p className="mt-1 text-1 text-text-muted">Offered: {beat.options.join(' · ')}</p>
                ) : null}
                {beat.answered ? (
                  <p className="mt-2 text-2 font-semibold">{beat.answered}</p>
                ) : null}
                {beat.safetyStatus === 'rewritten' ? (
                  <p className="mt-3 text-1 text-text-muted">
                    Our safety layer rewrote this scene before your child saw it. What you are
                    reading is what they saw.
                  </p>
                ) : null}
              </Card>
            </li>
          ))}
        </ol>
      </Section>
    </DashboardShell>
  );
}
