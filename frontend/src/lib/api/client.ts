/**
 * Thin fetch wrapper over the generated OpenAPI types.
 *
 * `schema.ts` is generated from the backend's `/api/schema/` (`make schema &&
 * make client`) — never edit it by hand. Everything here stays typed against
 * that file, so a backend response shape change breaks the build rather than
 * the child's screen.
 *
 * The client knows *how* to attach a bearer token and *when* to ask for a new
 * one, but it never stores either. The auth store injects those two callbacks
 * through `connectAuth`, which keeps the dependency pointing one way: store →
 * client, never back.
 */

import type { components, paths } from './schema';

/** Same-origin in dev via the Vite proxy; overridable for a split deploy. */
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

const V1 = '/api/v1';

export type Health = paths['/api/health/']['get']['responses'][200]['content']['application/json'];
export type User = components['schemas']['User'];
export type Me = components['schemas']['Me'];
export type Child = components['schemas']['Child'];
export type ChildRequest = components['schemas']['ChildRequest'];
export type PatchedChildRequest = components['schemas']['PatchedChildRequest'];
/** The parent's controls for one child (§7.13, §12.7). */
export type ChildSettings = components['schemas']['ChildSettings'];
export type PatchedChildSettingsRequest = components['schemas']['PatchedChildSettingsRequest'];
export type Accessibility = components['schemas']['Accessibility'];
/** May a story start right now (§13.6)? */
export type Wellbeing = components['schemas']['Wellbeing'];
/** The plain-language privacy statement (§12.8). */
export type PrivacyDisclosure = components['schemas']['PrivacyDisclosure'];
export type SignupRequest = components['schemas']['SignupRequest'];
export type SignupResponse = components['schemas']['SignupResponse'];
/** Login and refresh both return this: an access token, and nothing else. */
export type AccessToken = components['schemas']['AccessToken'];
export type ConsentRecord = components['schemas']['ConsentRecord'];
export type ConsentRecordRequest = components['schemas']['ConsentRecordRequest'];
export type Companion = components['schemas']['Companion'];
export type CompanionRequest = components['schemas']['CompanionRequest'];
export type CompanionCatalog = components['schemas']['CompanionCatalog'];
/** What the companion says when a child arrives — §4.5's day-two payoff. */
export type Arrival = components['schemas']['Arrival'];
export type AdventureStart = components['schemas']['AdventureStart'];
export type ChoiceRequest = components['schemas']['ChoiceRequestRequest'];
export type ChoiceAccepted = components['schemas']['ChoiceAccepted'];
export type AdventureEnd = components['schemas']['AdventureEnd'];
/** Parent dashboard (§12). Reports, insights, transparency. */
export type SessionReport = components['schemas']['SessionReport'];
export type SessionReportSummary = components['schemas']['SessionReportSummary'];
export type WeeklyReport = components['schemas']['WeeklyReport'];
export type Progress = components['schemas']['Progress'];
export type Insights = components['schemas']['Insights'];
export type Milestone = components['schemas']['Milestone'];
export type ParentMemory = components['schemas']['Memory'];
export type Transcript = components['schemas']['Transcript'];
export type TranscriptSummary = components['schemas']['TranscriptSummary'];
export type PaletteColors = components['schemas']['PaletteColors'];
export type ArchetypeOption = components['schemas']['ArchetypeOption'];
/** Catalogue keys. Unions of literals, so a typo is a build error. */
export type ArchetypeKey = components['schemas']['ArchetypeEnum'];
export type PaletteKey = components['schemas']['PaletteEnum'];
export type PersonalityPresetKey = components['schemas']['PersonalityPresetEnum'];

/**
 * Status used when the request never reached a server at all.
 *
 * `fetch` rejects rather than resolving for a dropped connection, a dead dev
 * proxy or DNS failure, so without this every caller has to handle two unrelated
 * error shapes and most will forget. "We could not reach it" is a distinct
 * outcome from any HTTP answer and a parent needs to be told which one happened.
 */
export const NETWORK_ERROR = 0;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The parsed DRF error body, when there was one — used to label form fields. */
    readonly body?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * How long to wait, in whole minutes, from DRF's throttle detail.
 *
 * The wire format is `"Request was throttled. Expected available in 2428
 * seconds."` — true, and useless to read. Returns null when the shape is anything
 * else, so an unrecognised message falls through to a plain sentence.
 */
function throttleWaitMinutes(body: unknown): number | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail !== 'string') return null;
  const seconds = /available in (\d+) seconds?/.exec(detail)?.[1];
  return seconds ? Math.max(1, Math.ceil(Number(seconds) / 60)) : null;
}

/**
 * A human sentence for an error, for a *parent* to read.
 *
 * DRF answers with either `{field: ["…"]}` or `{detail: "…"}`. Nothing here is
 * ever shown on the child surface — the child never sees an error at all (§13).
 *
 * Two rules, both learned the hard way on the login form:
 *
 * 1. **Only a parsed object is a message.** A non-JSON body is not something a
 *    person should read: an unreachable database made Django answer login with a
 *    full HTML debug traceback, this function returned the string as-is, and the
 *    login form rendered the traceback at the parent. In production the same
 *    branch would render a proxy's 502 page. If it did not parse as a DRF error,
 *    it is not an explanation.
 * 2. **Say which kind of failure it was.** The caller's `fallback` describes the
 *    *expected* failure — wrong password, say — so using it for a 500 or an outage
 *    tells the parent something false about their own account, which is how a
 *    working password ends up looking broken. Statuses that mean "not your fault"
 *    are answered here instead, and outrank any caller's guess.
 */
export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (!(error instanceof ApiError)) return fallback;

  if (error.status === NETWORK_ERROR) {
    return 'We could not reach The Primer. Check your connection and try again.';
  }
  if (error.status === 429) {
    const minutes = throttleWaitMinutes(error.body);
    return minutes === null
      ? 'That has been tried too many times just now. Please wait a little and try again.'
      : `That has been tried too many times just now. Please try again in ${minutes} minute${minutes === 1 ? '' : 's'}.`;
  }
  if (error.status >= 500) {
    return 'Something went wrong on our side, not with your details. Please try again in a moment.';
  }

  if (error.body && typeof error.body === 'object') {
    const record = error.body as Record<string, unknown>;
    if (typeof record.detail === 'string') return record.detail;
    for (const value of Object.values(record)) {
      if (typeof value === 'string') return value;
      if (Array.isArray(value) && typeof value[0] === 'string') return value[0];
    }
  }
  if (error.status === 401) return 'That email and password did not match.';
  return fallback;
}

interface AuthBridge {
  /** The current access token, or null when signed out. */
  accessToken: () => string | null;
  /** Exchange the refresh token for a new access token; null if that failed. */
  refresh: () => Promise<string | null>;
  /** Called when the session is definitively over, so the UI can redirect. */
  onSessionLost: () => void;
}

let bridge: AuthBridge = {
  accessToken: () => null,
  refresh: async () => null,
  onSessionLost: () => {},
};

export function connectAuth(next: AuthBridge): void {
  bridge = next;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  /** What we will take back. Only the beat stream wants anything but JSON. */
  accept?: string;
  /** Send the bearer token and retry once after a silent refresh. */
  authenticated?: boolean;
  /**
   * Send and accept the refresh cookie.
   *
   * Only the four `/auth/*` endpoints need this: the cookie is path-scoped
   * server-side, so the browser would not attach it elsewhere anyway, and saying
   * so per call documents which requests carry a credential the client cannot
   * see. Without it `fetch` drops the `Set-Cookie` on a cross-origin deploy and
   * the session silently stops surviving reloads.
   */
  withCookie?: boolean;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function send(path: string, options: RequestOptions, token: string | null) {
  const headers: Record<string, string> = { Accept: options.accept ?? 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;

  try {
    return await fetch(`${BASE_URL}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      credentials: options.withCookie ? 'include' : 'same-origin',
    });
  } catch (caught) {
    // An abort is the caller getting what it asked for, not a failure — React
    // Query cancels in-flight queries constantly and must keep seeing its own
    // `AbortError` rather than an `ApiError` it would then report.
    if (caught instanceof DOMException && caught.name === 'AbortError') throw caught;
    // Everything else here is "never reached a server": offline, dead proxy, DNS.
    // Given a status, callers can tell that apart from an answer they were given.
    throw new ApiError(`${options.method ?? 'GET'} ${path} could not be sent`, NETWORK_ERROR);
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const authenticated = options.authenticated ?? true;
  let response = await send(path, options, authenticated ? bridge.accessToken() : null);

  // One silent refresh, then one retry. Access tokens live ~15 minutes, so a
  // parent reading the dashboard will hit this routinely; it must be invisible.
  if (response.status === 401 && authenticated) {
    const refreshed = await bridge.refresh();
    if (refreshed) {
      response = await send(path, options, refreshed);
    } else {
      bridge.onSessionLost();
    }
  }

  const body = await parseBody(response);
  if (!response.ok) {
    throw new ApiError(`${options.method ?? 'GET'} ${path} failed`, response.status, body);
  }
  return body as T;
}

/**
 * An authenticated request whose body this module must **not** read.
 *
 * `request()` parses the whole body to return typed JSON, which is exactly wrong
 * for the two calls that are not JSON: the beat stream, which must not be
 * buffered, and the data export, which is a binary archive. Both still need the
 * silent-refresh-and-retry that `request()` provides — without it, an access token
 * that expired in the seconds before the call left the child looking at a thinking
 * companion forever (that is the real bug this was extracted from), and would
 * leave a parent with a failed download for the same reason.
 *
 * The response is returned unread; the caller decides what to do with it.
 */
async function sendAuthenticated(path: string, options: RequestOptions): Promise<Response> {
  let response = await send(path, options, bridge.accessToken());
  if (response.status === 401) {
    const refreshed = await bridge.refresh();
    if (refreshed) {
      response = await send(path, options, refreshed);
    } else {
      bridge.onSessionLost();
    }
  }
  return response;
}

/** Open the beat stream (§18.5). See `sendAuthenticated`. */
export function openStream(path: string, signal?: AbortSignal): Promise<Response> {
  return sendAuthenticated(path, { accept: 'text/event-stream', signal });
}

export const api = {
  health: (signal?: AbortSignal) =>
    request<Health>('/api/health/', { signal, authenticated: false }),

  signup: (payload: SignupRequest) =>
    request<SignupResponse>(`${V1}/auth/signup`, {
      method: 'POST',
      body: payload,
      authenticated: false,
      withCookie: true,
    }),

  login: (email: string, password: string) =>
    request<AccessToken>(`${V1}/auth/login`, {
      method: 'POST',
      body: { email, password },
      authenticated: false,
      withCookie: true,
    }),

  /**
   * Spend the refresh cookie for a new access token.
   *
   * No argument and no body: the credential is the `httpOnly` cookie, which the
   * browser attaches and this code cannot read. Rotation replaces it via
   * `Set-Cookie`, so there is nothing to store on the way back either.
   */
  refresh: () =>
    request<AccessToken>(`${V1}/auth/refresh`, {
      method: 'POST',
      authenticated: false,
      withCookie: true,
    }),

  /** Blacklist the session server-side and clear the cookie. */
  logout: () => request<null>(`${V1}/auth/logout`, { method: 'POST', withCookie: true }),

  me: (signal?: AbortSignal) => request<Me>(`${V1}/me`, { signal }),

  recordConsent: (payload: ConsentRecordRequest) =>
    request<ConsentRecord>(`${V1}/consent`, { method: 'POST', body: payload }),

  listChildren: (signal?: AbortSignal) => request<Child[]>(`${V1}/children`, { signal }),

  createChild: (payload: ChildRequest) =>
    request<Child>(`${V1}/children`, { method: 'POST', body: payload }),

  updateChild: (id: string, payload: PatchedChildRequest) =>
    request<Child>(`${V1}/children/${id}`, { method: 'PATCH', body: payload }),

  deleteChild: (id: string) => request<null>(`${V1}/children/${id}`, { method: 'DELETE' }),

  // --- settings & controls (§12.7) -----------------------------------------

  /** This child's controls, **as the safety layer will actually enforce them**. */
  childSettings: (childId: string, signal?: AbortSignal) =>
    request<ChildSettings>(`${V1}/children/${childId}/settings`, { signal }),

  /**
   * Change one or more controls (§12.7).
   *
   * Takes effect on the next generation — there is no cache anywhere between this
   * and the prompt assembler, the safety classifier or the deterministic guard.
   * `accessibility` is merged server-side, so sending one flag changes one flag.
   */
  updateChildSettings: (childId: string, payload: PatchedChildSettingsRequest) =>
    request<ChildSettings>(`${V1}/children/${childId}/settings`, {
      method: 'PATCH',
      body: payload,
    }),

  /**
   * May a story start right now (§13.6)?
   *
   * Asked by the child surface *before* it asks for an adventure, so a closed
   * time window becomes a warm scene instead of a failed request. Advisory here
   * and enforced on the server: the start endpoint refuses a new story outside
   * the window whether or not this was called.
   */
  wellbeing: (childId: string, signal?: AbortSignal) =>
    request<Wellbeing>(`${V1}/children/${childId}/wellbeing`, { signal }),

  // --- privacy centre (§12.8) ----------------------------------------------

  /** The plain-language disclosure. Readable without an account, deliberately. */
  privacy: (signal?: AbortSignal) =>
    request<PrivacyDisclosure>(`${V1}/privacy`, { signal, authenticated: false }),

  /**
   * Download everything we hold about this family, as a zip (§12.8).
   *
   * The one call that does not go through `request()`: that function parses the
   * body as JSON, and this body is a binary archive. A POST rather than a GET so
   * it is never a cacheable, history-recorded, cross-site-fetchable URL — see the
   * view's own docstring.
   *
   * Returns the blob and the filename the server chose, so the caller can save it
   * without inventing a name.
   */
  exportAccount: async (): Promise<{ blob: Blob; filename: string }> => {
    const response = await sendAuthenticated(`${V1}/account/export`, {
      method: 'POST',
      accept: 'application/zip',
    });
    if (!response.ok) {
      throw new ApiError(
        `POST ${V1}/account/export failed`,
        response.status,
        await parseBody(response),
      );
    }
    const disposition = response.headers.get('Content-Disposition') ?? '';
    const named = /filename="([^"]+)"/.exec(disposition)?.[1];
    return { blob: await response.blob(), filename: named ?? 'primer-export.zip' };
  },

  /**
   * Delete the account and every child's data (§12.8). Irreversible.
   *
   * The password is re-checked server-side: an access token on a shared tablet
   * must not be enough to erase a family's stories.
   */
  deleteAccount: (password: string) =>
    request<null>(`${V1}/account`, { method: 'DELETE', body: { password } }),

  companionCatalog: (signal?: AbortSignal) =>
    request<CompanionCatalog>(`${V1}/companion-catalog`, { signal }),

  /** The companion, or a 404 while the child has none yet. */
  companion: (childId: string, signal?: AbortSignal) =>
    request<Companion>(`${V1}/children/${childId}/companion`, { signal }),

  /**
   * Create the companion. Returns as soon as the identity is saved — the
   * portrait, expressions and voice are generated on a worker, so callers poll
   * until `assets_ready`.
   */
  createCompanion: (childId: string, payload: CompanionRequest) =>
    request<Companion>(`${V1}/children/${childId}/companion`, {
      method: 'POST',
      body: payload,
    }),

  /**
   * What the companion says when this child arrives (§4.5).
   *
   * `kind` is `returning` when the companion has something specific to remember
   * and `first_meeting` when it does not — a brand-new child, or any failure on
   * the generated path. The client renders those as different scenes and must
   * never infer which it is holding from the text.
   *
   * It costs a language call and a voice call, so it is asked **once per arrival**
   * and never polled. `audio_url` may be null; the scene is built to work in
   * silence (§13).
   */
  greeting: (childId: string, signal?: AbortSignal) =>
    request<Arrival>(`${V1}/children/${childId}/greeting`, { signal }),

  /**
   * The adventure this child is in the middle of, or `null` (a 204) if there is
   * none (§18.4).
   *
   * This is what makes a reload land back in the same story. The adventure id
   * lives only in a React Query cache, which is memory-only, so a hard refresh
   * forgets it — and a client that has forgotten which story it was telling used
   * to start a new one. Asking is a read: it cannot cost a generation however
   * often a buggy client does it.
   */
  currentAdventure: (childId: string, signal?: AbortSignal) =>
    request<AdventureStart | null>(`${V1}/children/${childId}/adventures/current`, { signal }),

  /**
   * Begin an adventure — or rejoin the one already in progress (§18.4).
   *
   * Returns before the first beat exists: the plan is saved, and `streamUrl` is
   * where the beats arrive. The client is meant to open that stream immediately —
   * the generation latency is hidden behind the companion's thinking animation,
   * not behind this request.
   *
   * Idempotent server-side while the child has an `in_progress` adventure, which
   * is the backstop under `currentAdventure`: even a client that skips the read
   * and POSTs twice gets one story.
   */
  beginAdventure: (childId: string) =>
    request<AdventureStart>(`${V1}/children/${childId}/adventures`, {
      method: 'POST',
      body: {},
    }),

  /**
   * Send back what the child just did (§18.6).
   *
   * Answers `202` before anything is generated: the tap has to be acknowledged on
   * screen within about 100ms (§14.7) and the beat that reacts to it takes
   * seconds, so the consequence arrives on the *stream*, not here. The client
   * reopens the stream as soon as this resolves.
   *
   * Sent for every interaction, including a beat that only asked the child to
   * carry on — `latencyMs`, `retries` and `assisted` are wellbeing and difficulty
   * signals we want for all of them, not only for branches.
   */
  submitChoice: (adventureId: string, payload: ChoiceRequest) =>
    request<ChoiceAccepted>(`${V1}/adventures/${adventureId}/choice`, {
      method: 'POST',
      body: payload,
    }),

  /**
   * Close the adventure and the sitting (§13.6).
   *
   * `completed` when the child reached the warm close, `exit` when they left
   * before it. Idempotent, so the tap on "all done" and the page unloading cannot
   * disagree about when the sitting ended.
   */
  endAdventure: (adventureId: string, reason: 'completed' | 'exit' = 'completed') =>
    request<AdventureEnd>(`${V1}/adventures/${adventureId}/end`, {
      method: 'POST',
      body: { reason },
    }),

  // --- parent dashboard (§12) ----------------------------------------------
  // All eight are plain authenticated reads and all eight are owner-scoped
  // server-side. Nothing here is ever called from the child tree.

  /** This child's session reports, newest first (§12.10). */
  sessionReports: (childId: string, signal?: AbortSignal) =>
    request<SessionReportSummary[]>(`${V1}/children/${childId}/reports/sessions`, { signal }),

  /**
   * The report for one sitting (§12.1).
   *
   * Generated after the sitting ends; asking for one that was never generated
   * generates it, so a worker outage cannot leave a hole in the dashboard. A
   * sitting still in progress answers 404 — there is nothing to recap yet.
   */
  sessionReport: (childId: string, sessionId: string, signal?: AbortSignal) =>
    request<SessionReport>(`${V1}/children/${childId}/reports/session/${sessionId}`, { signal }),

  /** The week's digest (§12.2, §18.7). `week` is any date inside the week wanted. */
  weeklyReport: (childId: string, week?: string, signal?: AbortSignal) =>
    request<WeeklyReport>(
      `${V1}/children/${childId}/reports/weekly${week ? `?week=${week}` : ''}`,
      { signal },
    ),

  /** Per-objective progress in plain language (§12.3). Never rendered as a number. */
  progress: (childId: string, signal?: AbortSignal) =>
    request<Progress[]>(`${V1}/children/${childId}/progress`, { signal }),

  /** Milestones and the interest trend (§12.3, §12.5, §11.7). */
  insights: (childId: string, signal?: AbortSignal) =>
    request<Insights>(`${V1}/children/${childId}/insights`, { signal }),

  /** What the companion remembers about this child (§12.6). */
  memories: (childId: string, signal?: AbortSignal) =>
    request<ParentMemory[]>(`${V1}/children/${childId}/memories`, { signal }),

  /** The transparency log's index — every story this child has played (§12.6). */
  transcripts: (childId: string, signal?: AbortSignal) =>
    request<TranscriptSummary[]>(`${V1}/children/${childId}/transcripts`, { signal }),

  /** One story, verbatim (§12.6). */
  transcript: (adventureId: string, signal?: AbortSignal) =>
    request<Transcript>(`${V1}/adventures/${adventureId}/transcript`, { signal }),
};
