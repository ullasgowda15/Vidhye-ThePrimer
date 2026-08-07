/**
 * Wraps the whole `/parent` subtree behind the adult challenge (§14.2).
 *
 * Deliberately outside `ProtectedRoute`: the gate comes *first*, so a child
 * cannot even reach the login form, and a parent signing up for the first time
 * still meets it. Login is not the gate — a signed-in parent who hands the
 * tablet to their child is exactly the case this guards.
 */

import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { useAuthStore } from '@/store/auth';

export default function AdultGateRoute() {
  const location = useLocation();
  // Subscribing to the timestamp (not the selector result) so a component
  // re-renders when the gate is passed or cleared.
  const verifiedUntil = useAuthStore((state) => state.parentVerifiedUntil);
  const verified = verifiedUntil !== null && verifiedUntil > Date.now();

  if (!verified) {
    return (
      <Navigate to="/parent/gate" replace state={{ from: location.pathname + location.search }} />
    );
  }

  return <Outlet />;
}
