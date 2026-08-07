/**
 * Thin fetch wrapper over the generated OpenAPI types.
 *
 * `schema.ts` is generated from the backend's `/api/schema/` (`make schema &&
 * make client`) — never edit it by hand. Everything here stays typed against
 * that file, so a backend response shape change breaks the build rather than
 * the child's screen.
 */

import type { paths } from './schema';

/** Same-origin in dev via the Vite proxy; overridable for a split deploy. */
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    credentials: 'include',
    signal,
  });

  if (!response.ok) {
    throw new ApiError(`GET ${path} failed`, response.status);
  }
  return (await response.json()) as T;
}

export type Health = paths['/api/health/']['get']['responses'][200]['content']['application/json'];

export const api = {
  health: (signal?: AbortSignal) => get<Health>('/api/health/', signal),
};
