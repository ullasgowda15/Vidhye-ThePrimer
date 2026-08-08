/**
 * Requires a signed-in parent (PRODUCT.md §18.2).
 *
 * Client-side routing is convenience, never enforcement — every endpoint behind
 * these screens re-checks ownership server-side. What this buys is that an
 * unauthenticated visitor sees the login form instead of an empty dashboard
 * flashing three failed requests.
 *
 * **The `loading` branch is the part that gets built wrong.** The session is
 * restored by one refresh call at app load (§14.5), so for the first moment of
 * every page load there is no access token *and no answer yet*. A guard that
 * checks the token alone treats that moment as "signed out" and redirects — so
 * every reload flashes the login screen before bouncing back to the dashboard.
 * Three states, not two, is the fix: `loading` holds still.
 *
 * It holds still on a blank surface rather than a "checking…" message: the wait is
 * one request long, and a message that appears and vanishes is worse than nothing
 * appearing at all.
 */

import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuthStore } from '@/store/auth';

export default function ProtectedRoute() {
  const location = useLocation();
  const status = useAuthStore((state) => state.status);

  if (status === 'loading') {
    return <div data-testid="session-restoring" className="min-h-full bg-bg" />;
  }

  if (status === 'anonymous') {
    return (
      <Navigate to="/parent/login" replace state={{ from: location.pathname + location.search }} />
    );
  }

  return <Outlet />;
}
