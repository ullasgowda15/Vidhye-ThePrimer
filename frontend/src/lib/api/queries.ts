import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { useAuthStore } from '@/store/auth';

import {
  ApiError,
  api,
  type ChildRequest,
  type CompanionRequest,
  type ConsentRecordRequest,
} from './client';

export const queryKeys = {
  health: ['health'] as const,
  me: ['me'] as const,
  children: ['children'] as const,
  companionCatalog: ['companion-catalog'] as const,
  companion: (childId: string) => ['companion', childId] as const,
  adventure: (childId: string) => ['adventure', childId] as const,
};

/**
 * How often the greeting scene asks whether the companion has finished waking up.
 *
 * It **backs off**, and that is the point rather than a refinement. A fixed
 * two-second poll asks thirty times a minute for as long as the page is open, and
 * the asset build it is waiting for takes a minute or more — so a flat poll is
 * guaranteed to send far more requests than the thing it is watching needs, and
 * will find whatever ceiling exists on the endpoint. It found ours.
 *
 * Starting fast keeps the imprint moment responsive (assets usually land while the
 * interval is still a few seconds); settling at ten seconds bounds the steady
 * state to six requests a minute, comfortably inside the read scope's 480/hour.
 */
export const COMPANION_POLL_MIN_MS = 2_000;
export const COMPANION_POLL_MAX_MS = 10_000;
const COMPANION_POLL_BACKOFF = 1.5;

/**
 * A 429 means we have already asked too often. Asking again *is* the bug — it is
 * what turns one rate limit into a permanent one, since a denied request still
 * arrives at the server. So it ends the poll rather than slowing it.
 */
function isThrottled(error: unknown): boolean {
  return error instanceof ApiError && error.status === 429;
}

/**
 * The wait before the next poll, after `attempts` completed round trips.
 *
 * Exported because the numbers, not the code, are the thing worth checking: this
 * function's steady state has to fit inside the `companion_read` budget the
 * backend grants (`config/settings/base.py`). That coupling crosses the stack and
 * nothing enforces it automatically, so its test states it out loud.
 */
export function companionPollInterval(attempts: number): number {
  return Math.min(
    COMPANION_POLL_MIN_MS * COMPANION_POLL_BACKOFF ** attempts,
    COMPANION_POLL_MAX_MS,
  );
}

/**
 * Backend liveness. Drives the unobtrusive status dot in the child footer.
 *
 * Retry policy is deliberately left to the QueryClient: a child's tablet drops
 * wifi constantly, and that is one decision for the whole app, not a per-query
 * one. Failure is handled by *rendering* — the dot goes amber and the story
 * shell stays up (§13). The child never sees an error.
 */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => api.health(signal),
  });
}

/** The signed-in parent and their children (§18.3 `/me`). */
export function useMe() {
  const authenticated = useAuthStore((state) => state.accessToken !== null);

  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => api.me(signal),
    enabled: authenticated,
  });
}

export function useChildren() {
  const authenticated = useAuthStore((state) => state.accessToken !== null);

  return useQuery({
    queryKey: queryKeys.children,
    queryFn: ({ signal }) => api.listChildren(signal),
    enabled: authenticated,
  });
}

export function useRecordConsent() {
  return useMutation({
    mutationFn: (payload: ConsentRecordRequest) => api.recordConsent(payload),
  });
}

export function useCreateChild() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ChildRequest) => api.createChild(payload),
    onSuccess: async () => {
      // `/me` embeds the children, so both caches go stale on a write.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.children }),
        queryClient.invalidateQueries({ queryKey: queryKeys.me }),
      ]);
    },
  });
}

/** Archetypes, palettes and personality presets for the picker (§14.5). */
export function useCompanionCatalog() {
  const authenticated = useAuthStore((state) => state.accessToken !== null);

  return useQuery({
    queryKey: queryKeys.companionCatalog,
    queryFn: ({ signal }) => api.companionCatalog(signal),
    enabled: authenticated,
    // A fixed list that changes on deploys, not during a session.
    staleTime: Infinity,
  });
}

/**
 * One child's companion.
 *
 * Polls with backoff while the art and the voice are still being generated, and
 * stops as soon as they land. Two statuses are not "try again":
 *
 * - **404** — "no companion yet", a normal state during onboarding rather than an
 *   error, so it is never retried into a spinner. The poll continues, because the
 *   parent may be creating one right now.
 * - **429** — we have already asked too often, so the poll ends. Retrying a rate
 *   limit is what makes it permanent.
 *
 * Neither is ever shown to the child: the greeting scene renders a sleeping friend
 * whatever this hook returns (§13).
 */
export function useCompanion(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.companion(childId ?? ''),
    queryFn: ({ signal }) => api.companion(childId as string, signal),
    enabled: Boolean(childId),
    retry: (failureCount, error) =>
      !(error instanceof ApiError && (error.status === 404 || error.status === 429)) &&
      failureCount < 2,
    refetchInterval: (query) => {
      if (query.state.data?.assets_ready) return false;
      if (isThrottled(query.state.error)) return false;
      // Every completed round trip lengthens the next wait, successes and
      // failures alike — a poll that only backed off on success would keep
      // hammering exactly when something is already wrong.
      return companionPollInterval(query.state.dataUpdateCount + query.state.errorUpdateCount);
    },
  });
}

export function useCreateCompanion(childId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CompanionRequest) => api.createCompanion(childId, payload),
    onSuccess: (companion) => {
      // Seed the cache so the greeting scene opens on the companion it just
      // created rather than on a fresh request.
      queryClient.setQueryData(queryKeys.companion(childId), companion);
    },
  });
}

/**
 * Begin an adventure for this child (§18.4).
 *
 * A `useQuery` for something that POSTs, deliberately. React Query holds one
 * in-flight request per key, and that is the property this call needs more than
 * mutation semantics: React 18 runs effects twice on mount in development, and
 * without deduplication that is two adventures — two plans, two beats, two
 * illustrations, two voice lines — every time a developer opens the story page.
 *
 * `staleTime: Infinity` for the same reason: within one mount, the adventure that
 * was started *is* the adventure. Starting a new one is navigating here again.
 * The backend replays an adventure's existing beat rather than regenerating it, so
 * a reload lands the child back in the same story either way.
 */
export function useBeginAdventure(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.adventure(childId ?? ''),
    queryFn: () => api.beginAdventure(childId as string),
    enabled: Boolean(childId),
    staleTime: Infinity,
    // One retry. A story that silently starts three times over a flaky connection
    // costs three plans; the child sees the idle companion either way.
    retry: 1,
    refetchOnMount: false,
  });
}

export function useDeleteChild() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => api.deleteChild(id),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.children }),
        queryClient.invalidateQueries({ queryKey: queryKeys.me }),
      ]);
    },
  });
}
