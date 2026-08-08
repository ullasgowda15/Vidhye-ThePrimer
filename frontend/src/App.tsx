import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect } from 'react';
import { RouterProvider } from 'react-router-dom';

import { router } from './router';
import { useAuthStore } from './store/auth';

/**
 * TanStack Query is the server cache; Zustand (src/store) holds the little
 * local state that isn't server data (PRODUCT.md §16.1).
 *
 * A child's tablet drops off wifi mid-story, so retries and a generous stale
 * time are the default rather than a per-query decision. Refetch-on-focus is
 * off: nothing should change under a child's hands because they tapped away.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  const bootstrap = useAuthStore((state) => state.bootstrap);

  /**
   * Session restore, and the only place it happens (§14.5).
   *
   * One POST to `/auth/refresh`, which spends the httpOnly cookie for an access
   * token. This is what makes a cold start survivable for a child who cannot log
   * in — the tab was evicted, the tablet rebooted, it is the next morning, and the
   * story is still theirs.
   *
   * `bootstrap` is idempotent, which matters here: StrictMode runs this effect
   * twice in development, and a second refresh would present a cookie the first
   * had already rotated and blacklisted.
   */
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>
  );
}
