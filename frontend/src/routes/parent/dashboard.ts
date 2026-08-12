/**
 * The parent dashboard's non-visual bits (PRODUCT.md §12).
 *
 * A separate module from `ui.tsx` and `DashboardShell.tsx` because those two are
 * component files: a hook or a lookup table exported alongside a component breaks
 * Vite's fast refresh for the whole file, and the dashboard is the surface a
 * developer edits most while looking at it.
 */

import { useMe } from '@/lib/api/queries';

export interface ChildSummary {
  id: string;
  first_name: string;
}

/**
 * The child named in the URL, from the account we already loaded.
 *
 * Read out of `/me` rather than fetched per page: the parent's children arrive
 * with the account, and a second request per dashboard page to learn a first name
 * would be a request per page for something we are holding. `undefined` while the
 * account is loading and `null` when the id is not one of this parent's children —
 * two different states, and the pages render them differently.
 */
export function useDashboardChild(childId: string | undefined): ChildSummary | null | undefined {
  const { data: me, isLoading } = useMe();
  if (isLoading || !me) return undefined;
  return me.children.find((child) => child.id === childId) ?? null;
}

/** A date a parent reads, not an ISO string. */
export function when(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });
}

/**
 * How a sitting ended, in a parent's words (§13.6).
 *
 * A short session is not a failed one, so none of these three reads as a failure.
 * They are kept distinct rather than collapsed into "played" because
 * `ended_reason` is a wellbeing signal and rounding it up would be the first place
 * the dashboard stopped being honest.
 */
export const ENDING: Record<string, string> = {
  completed: 'Played to the end',
  exit: 'Left partway through',
  cap: 'Time was up',
};

/**
 * The four status words (§12.3), as words.
 *
 * There is no fifth entry and there never will be one that means "behind": the
 * vocabulary a parent is shown has no failure state, because at three to seven
 * there is no such thing as behind schedule for a skill nobody has taught yet.
 */
export const STATUS_LABEL: Record<string, string> = {
  not_started: 'Not covered yet',
  emerging: 'Just starting',
  practising: 'Practising',
  mastered: 'Does this reliably',
};

export const STATUS_STYLE: Record<string, string> = {
  not_started: 'border-border text-text-muted',
  emerging: 'border-border text-text',
  practising: 'border-accent text-accent',
  mastered: 'border-success text-success',
};
