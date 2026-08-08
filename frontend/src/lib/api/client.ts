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
export type SignupRequest = components['schemas']['SignupRequest'];
export type SignupResponse = components['schemas']['SignupResponse'];
/** Login and refresh both return this: an access token, and nothing else. */
export type AccessToken = components['schemas']['AccessToken'];
export type ConsentRecord = components['schemas']['ConsentRecord'];
export type ConsentRecordRequest = components['schemas']['ConsentRecordRequest'];
export type Companion = components['schemas']['Companion'];
export type CompanionRequest = components['schemas']['CompanionRequest'];
export type CompanionCatalog = components['schemas']['CompanionCatalog'];
export type AdventureStart = components['schemas']['AdventureStart'];
export type PaletteColors = components['schemas']['PaletteColors'];
export type ArchetypeOption = components['schemas']['ArchetypeOption'];
/** Catalogue keys. Unions of literals, so a typo is a build error. */
export type ArchetypeKey = components['schemas']['ArchetypeEnum'];
export type PaletteKey = components['schemas']['PaletteEnum'];
export type PersonalityPresetKey = components['schemas']['PersonalityPresetEnum'];

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
 * A human sentence for an error, for a *parent* to read.
 *
 * DRF answers with either `{field: ["…"]}` or `{detail: "…"}`. Nothing here is
 * ever shown on the child surface — the child never sees an error at all (§13).
 */
export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (!(error instanceof ApiError)) return fallback;
  const body = error.body;
  if (typeof body === 'string' && body) return body;
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>;
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
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;

  return fetch(`${BASE_URL}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    credentials: options.withCookie ? 'include' : 'same-origin',
  });
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
   * Begin an adventure (§18.4).
   *
   * Returns before the first beat exists: the plan is saved, and `streamUrl` is
   * where the beats arrive. The client is meant to open that stream immediately —
   * the generation latency is hidden behind the companion's thinking animation,
   * not behind this request.
   */
  beginAdventure: (childId: string) =>
    request<AdventureStart>(`${V1}/children/${childId}/adventures`, {
      method: 'POST',
      body: {},
    }),
};
