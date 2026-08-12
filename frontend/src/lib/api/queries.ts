import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { useAuthStore } from '@/store/auth';

import {
  ApiError,
  api,
  type ChildRequest,
  type CompanionRequest,
  type ConsentRecordRequest,
  type PatchedChildSettingsRequest,
} from './client';

export const queryKeys = {
  health: ['health'] as const,
  me: ['me'] as const,
  children: ['children'] as const,
  companionCatalog: ['companion-catalog'] as const,
  companion: (childId: string) => ['companion', childId] as const,
  greeting: (childId: string) => ['greeting', childId] as const,
  adventure: (childId: string) => ['adventure', childId] as const,
  sessionReports: (childId: string) => ['session-reports', childId] as const,
  sessionReport: (childId: string, sessionId: string) =>
    ['session-report', childId, sessionId] as const,
  weeklyReport: (childId: string, week?: string) =>
    ['weekly-report', childId, week ?? 'current'] as const,
  progress: (childId: string) => ['progress', childId] as const,
  insights: (childId: string) => ['insights', childId] as const,
  memories: (childId: string) => ['memories', childId] as const,
  transcripts: (childId: string) => ['transcripts', childId] as const,
  transcript: (adventureId: string) => ['transcript', adventureId] as const,
  childSettings: (childId: string) => ['child-settings', childId] as const,
  wellbeing: (childId: string) => ['wellbeing', childId] as const,
  privacy: ['privacy'] as const,
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
 * What the companion says when this child arrives (§4.5) — the day-two payoff.
 *
 * **Asked once per arrival, and never polled.** Every other read on the child
 * surface is cheap and repeatable; this one is a language call and a voice call,
 * so the settings that would ordinarily be tuning are load-bearing here:
 *
 * - `staleTime: Infinity` — within one visit, the hello *is* the hello. Refetching
 *   would generate a second greeting for a child already listening to the first.
 * - `refetchOnWindowFocus: false` — a parent tabbing back must not buy a new one.
 * - `retry: false` — the backend already falls back to the first-meeting hello for
 *   every failure it can see, so a failure that reaches *here* is the request
 *   itself, and retrying it spends the same money on the same outcome. The scene
 *   renders the companion arriving in silence, which is a scene rather than an
 *   error (§13).
 */
export function useGreeting(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.greeting(childId ?? ''),
    queryFn: ({ signal }) => api.greeting(childId as string, signal),
    enabled: Boolean(childId),
    staleTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/**
 * The adventure this child is in, starting one only if they are not in any (§18.4).
 *
 * **Resume first, begin second.** This used to POST straight away and lean on the
 * React Query cache to keep it from happening twice. That works within one mount
 * and not one step further: the cache is memory-only — there is no
 * `persistQueryClient` anywhere and deliberately so, since an adventure id is
 * server state — so a hard refresh forgot which story was being told and the page
 * began a new one. A plan, an opening beat, an illustration and a voice line, per
 * reload. The backend's replay path was correct the whole time and was simply never
 * asked for the same adventure twice.
 *
 * So the mount *asks*: `GET …/adventures/current` returns the in-progress adventure
 * or a 204, and only a 204 leads to a POST. The reload then reopens the same stream
 * and gets its beat from replay — no generation, no spend.
 *
 * Two belts remain, because this is the expensive thing to get wrong:
 *
 * - a `useQuery` rather than a mutation, so React Query holds one in-flight request
 *   per key and React 18's development double-mount is deduplicated rather than
 *   doubled;
 * - the POST itself is idempotent server-side while an adventure is `in_progress`,
 *   so even a client that skips the read entirely gets one story.
 *
 * `staleTime: Infinity`: within one mount, the adventure being told *is* the
 * adventure. Getting a different one means the current one finished.
 *
 * `ready` is the third belt, added on Day 13 for §13.6's time window: the caller
 * holds this off until it knows a story may start, so a child arriving after
 * bedtime never sends a request the server would refuse. The server refuses it
 * anyway (`story.views.RestingHours`) — this only decides whether we knock.
 */
export function useAdventure(childId: string | undefined, ready = true) {
  return useQuery({
    queryKey: queryKeys.adventure(childId ?? ''),
    queryFn: async ({ signal }) => {
      const resumed = await api.currentAdventure(childId as string, signal);
      return resumed ?? (await api.beginAdventure(childId as string));
    },
    enabled: Boolean(childId) && ready,
    staleTime: Infinity,
    // One retry. A story that silently starts three times over a flaky connection
    // costs three plans; the child sees the idle companion either way.
    retry: 1,
    refetchOnMount: false,
  });
}

/**
 * Forget the adventure just finished, so the next ask starts a fresh one.
 *
 * `staleTime: Infinity` is what makes `useAdventure` stable within a mount — the
 * adventure being told *is* the adventure — so the one moment it has to be
 * unlearned needs saying out loud. That moment is the warm close: the child taps
 * "again", the old adventure has been `end`ed server-side, and the next
 * `GET …/adventures/current` will answer 204 and lead to a new story.
 *
 * `removeQueries` rather than `invalidateQueries`: invalidating refetches while
 * the page is still mounted on the old data, and for a beat or two the client
 * would be streaming one adventure while holding another's id.
 */
export function useResetAdventure(childId: string | undefined) {
  const queryClient = useQueryClient();

  return () => {
    if (!childId) return;
    queryClient.removeQueries({ queryKey: queryKeys.adventure(childId) });
  };
}

/**
 * The parent dashboard's reads (§12).
 *
 * All eight share one posture, and it is the opposite of the child surface's.
 *
 * - **Errors are shown, not hidden.** The child never sees an error (§13); a
 *   parent reading a report about their own child has to be told plainly when we
 *   could not load it, or the dashboard's honesty is a claim about the happy path
 *   only. So none of these fall back to a friendly placeholder — the pages render
 *   `error` (see `Section` in `routes/parent/ui.tsx`).
 * - **Nothing polls.** These are documents, not live state. A parent leaving the
 *   tab open overnight must not send a request a minute at a surface that is only
 *   interesting once a day.
 * - **Session reports and transcripts never go stale**, because they are records
 *   of finished afternoons and cannot change. The weekly report and the insights
 *   *can* change — a sitting this evening moves both — so they are left on the
 *   default staleness and refetch when the parent comes back to them.
 */
const RECORD = { staleTime: Infinity, refetchOnWindowFocus: false } as const;

function enabled(childId: string | undefined) {
  return Boolean(childId);
}

export function useSessionReports(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.sessionReports(childId ?? ''),
    queryFn: ({ signal }) => api.sessionReports(childId as string, signal),
    enabled: enabled(childId),
  });
}

export function useSessionReport(childId: string | undefined, sessionId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.sessionReport(childId ?? '', sessionId ?? ''),
    queryFn: ({ signal }) => api.sessionReport(childId as string, sessionId as string, signal),
    enabled: enabled(childId) && Boolean(sessionId),
    ...RECORD,
  });
}

export function useWeeklyReport(childId: string | undefined, week?: string) {
  return useQuery({
    queryKey: queryKeys.weeklyReport(childId ?? '', week),
    queryFn: ({ signal }) => api.weeklyReport(childId as string, week, signal),
    enabled: enabled(childId),
  });
}

export function useProgress(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.progress(childId ?? ''),
    queryFn: ({ signal }) => api.progress(childId as string, signal),
    enabled: enabled(childId),
  });
}

export function useInsights(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.insights(childId ?? ''),
    queryFn: ({ signal }) => api.insights(childId as string, signal),
    enabled: enabled(childId),
  });
}

export function useMemories(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.memories(childId ?? ''),
    queryFn: ({ signal }) => api.memories(childId as string, signal),
    enabled: enabled(childId),
  });
}

export function useTranscripts(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.transcripts(childId ?? ''),
    queryFn: ({ signal }) => api.transcripts(childId as string, signal),
    enabled: enabled(childId),
  });
}

export function useTranscript(adventureId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.transcript(adventureId ?? ''),
    queryFn: ({ signal }) => api.transcript(adventureId as string, signal),
    enabled: Boolean(adventureId),
    ...RECORD,
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

/**
 * Settings & controls (§12.7), and the wellbeing gate (§13.6).
 */

/**
 * This child's controls, as the safety layer will actually enforce them.
 *
 * Read by two very different screens: the parent's settings form, and the child's
 * story page (for the accessibility flags). That is why it is one hook rather than
 * two endpoints — the settings a parent set and the settings the child experiences
 * must be the same object, or a toggle that appears to work is a toggle that does
 * not.
 *
 * `staleTime: 0` and a refetch on focus, deliberately against the pattern the
 * dashboard reads use: a parent may have changed a control on another device or in
 * another tab, and this is the one document in the product where showing a stale
 * value is showing a *wrong safety setting*.
 */
export function useChildSettings(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.childSettings(childId ?? ''),
    queryFn: ({ signal }) => api.childSettings(childId as string, signal),
    enabled: enabled(childId),
  });
}

export function useUpdateChildSettings(childId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: PatchedChildSettingsRequest) =>
      api.updateChildSettings(childId as string, payload),
    // Seeded from the response rather than refetched: the server answers with the
    // full normalised object, so the form immediately shows what will actually be
    // enforced — including a value it clamped.
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.childSettings(childId ?? ''), settings);
      // The child's profile carries the same three safety fields on `/children`,
      // so a settings change makes that cache wrong too.
      void queryClient.invalidateQueries({ queryKey: queryKeys.children });
    },
  });
}

/**
 * May a story start right now (§13.6)?
 *
 * Asked on the child surface before an adventure is requested, so a closed daily
 * window becomes a warm scene rather than a failed request. **Not polled** — a
 * countdown is precisely the pressure §13.6 forbids, and a child sitting in front
 * of a resting companion must not have the screen change under them because a
 * clock ticked over. It refetches when the page is revisited, which is when the
 * question is actually being asked again.
 */
export function useWellbeing(childId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.wellbeing(childId ?? ''),
    queryFn: ({ signal }) => api.wellbeing(childId as string, signal),
    enabled: enabled(childId),
    retry: 1,
  });
}

/**
 * The privacy disclosure (§12.8).
 *
 * Unauthenticated, and cached hard: it is a document that changes on deploys, not
 * during a session.
 */
export function usePrivacyDisclosure() {
  return useQuery({
    queryKey: queryKeys.privacy,
    queryFn: ({ signal }) => api.privacy(signal),
    ...RECORD,
  });
}

/**
 * Export everything (§12.8), then hand the browser the file.
 *
 * The save happens here rather than in the page because it is the same three
 * lines every caller would otherwise write, and because forgetting
 * `revokeObjectURL` leaks a whole archive of a family's data into the tab's memory
 * for as long as it stays open.
 */
export function useExportAccount() {
  return useMutation({
    mutationFn: async () => {
      const { blob, filename } = await api.exportAccount();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      return filename;
    },
  });
}

/**
 * Delete the account and everything in it (§12.8).
 *
 * `clear()` rather than invalidate: every cached query is about data that no
 * longer exists, and refetching them would be a burst of 401s on the way out.
 */
export function useDeleteAccount() {
  const queryClient = useQueryClient();
  const logout = useAuthStore((state) => state.logout);

  return useMutation({
    mutationFn: (password: string) => api.deleteAccount(password),
    onSuccess: async () => {
      queryClient.clear();
      await logout();
    },
  });
}
