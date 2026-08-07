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
export type TokenPair = components['schemas']['TokenObtainPair'];
export type TokenRefresh = components['schemas']['TokenRefresh'];
export type ConsentRecord = components['schemas']['ConsentRecord'];
export type ConsentRecordRequest = components['schemas']['ConsentRecordRequest'];

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
    }),

  login: (email: string, password: string) =>
    request<TokenPair>(`${V1}/auth/login`, {
      method: 'POST',
      body: { email, password },
      authenticated: false,
    }),

  /** Rotation returns a *new* refresh token too; callers must store both. */
  refresh: (refresh: string) =>
    request<TokenRefresh>(`${V1}/auth/refresh`, {
      method: 'POST',
      body: { refresh },
      authenticated: false,
    }),

  logout: (refresh: string) =>
    request<null>(`${V1}/auth/logout`, { method: 'POST', body: { refresh } }),

  me: (signal?: AbortSignal) => request<Me>(`${V1}/me`, { signal }),

  recordConsent: (payload: ConsentRecordRequest) =>
    request<ConsentRecord>(`${V1}/consent`, { method: 'POST', body: payload }),

  listChildren: (signal?: AbortSignal) => request<Child[]>(`${V1}/children`, { signal }),

  createChild: (payload: ChildRequest) =>
    request<Child>(`${V1}/children`, { method: 'POST', body: payload }),

  updateChild: (id: string, payload: PatchedChildRequest) =>
    request<Child>(`${V1}/children/${id}`, { method: 'PATCH', body: payload }),

  deleteChild: (id: string) => request<null>(`${V1}/children/${id}`, { method: 'DELETE' }),
};
