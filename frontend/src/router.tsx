import { createBrowserRouter } from 'react-router-dom';

import ChildHome from './routes/child/ChildHome';
import ParentHome from './routes/parent/ParentHome';

/**
 * Two route trees, deliberately separate (PRODUCT.md §14.2):
 *   `/`       the child experience — storybook, minimal chrome, Storyland theme
 *   `/parent` the parent dashboard — reports and controls, Calm theme
 *
 * They share tokens and primitives but never share a shell; a child must never
 * be one tap from billing, and a parent must never be handed a storybook.
 */
export const router = createBrowserRouter(
  [
    { path: '/', element: <ChildHome /> },
    { path: '/parent', element: <ParentHome /> },
  ],
  // Opt in to the v7 behaviours now, while there are two routes to check
  // rather than a whole story surface. (`v7_startTransition` is a
  // RouterProvider flag, not a router one — it lives in App.tsx.)
  {
    future: {
      v7_relativeSplatPath: true,
      v7_fetcherPersist: true,
      v7_normalizeFormMethod: true,
      v7_partialHydration: true,
      v7_skipActionErrorRevalidation: true,
    },
  },
);
