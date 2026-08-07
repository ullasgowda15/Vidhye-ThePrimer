/**
 * Requires a signed-in parent (PRODUCT.md §18.2).
 *
 * Client-side routing is convenience, never enforcement — every endpoint behind
 * these screens re-checks ownership server-side. What this buys is that an
 * unauthenticated visitor sees the login form instead of an empty dashboard
 * flashing three failed requests.
 */

import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuthStore } from '@/store/auth';

export default function ProtectedRoute() {
  const location = useLocation();
  const accessToken = useAuthStore((state) => state.accessToken);

  if (accessToken === null) {
    return (
      <Navigate to="/parent/login" replace state={{ from: location.pathname + location.search }} />
    );
  }

  return <Outlet />;
}
