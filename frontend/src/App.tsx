import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';

import { router } from './router';

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
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>
  );
}
