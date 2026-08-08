import { createBrowserRouter } from 'react-router-dom';

import ChildHome from './routes/child/ChildHome';
import MeetCompanion from './routes/child/MeetCompanion';
import StoryPage from './routes/child/StoryPage';
import AdultGateRoute from './routes/parent/AdultGateRoute';
import Login from './routes/parent/Login';
import Onboarding from './routes/parent/Onboarding';
import ParentGate from './routes/parent/ParentGate';
import ParentHome from './routes/parent/ParentHome';
import ProtectedRoute from './routes/parent/ProtectedRoute';
import Signup from './routes/parent/Signup';

/**
 * Two route trees, deliberately separate (PRODUCT.md §14.2):
 *   `/`       the child experience — storybook, minimal chrome, Storyland theme
 *   `/parent` the parent dashboard — reports and controls, Calm theme
 *
 * They share tokens and primitives but never share a shell; a child must never
 * be one tap from billing, and a parent must never be handed a storybook.
 *
 * `/parent` has two nested guards, in this order:
 *   1. `AdultGateRoute` — the adult challenge (§14.2). Everything under /parent
 *      is behind it, including login, so a child cannot even reach a form.
 *   2. `ProtectedRoute` — a signed-in parent. Login, signup and onboarding sit
 *      outside it, because they are how you become signed in.
 */
export const router = createBrowserRouter(
  [
    { path: '/', element: <ChildHome /> },
    // The imprint scene sits in the child tree, deliberately outside the parent
    // guards: the parent hands the tablet over here, and a child must never be
    // asked an adult challenge to meet their friend. It shows nothing an
    // unauthenticated request could obtain — without a session it stays a
    // sleeping companion.
    { path: '/meet/:childId', element: <MeetCompanion /> },
    // The story, in the child tree and outside the parent guards for the same
    // reason: the child is the one holding the tablet. It runs under the parent's
    // restored session; without one it stays a companion thinking, never a form.
    { path: '/story/:childId', element: <StoryPage /> },
    {
      path: '/parent',
      children: [
        { path: 'gate', element: <ParentGate /> },
        {
          element: <AdultGateRoute />,
          children: [
            { path: 'login', element: <Login /> },
            { path: 'signup', element: <Signup /> },
            { path: 'onboarding', element: <Onboarding /> },
            {
              element: <ProtectedRoute />,
              children: [{ index: true, element: <ParentHome /> }],
            },
          ],
        },
      ],
    },
  ],
  // Opt in to the v7 behaviours now, while there are a handful of routes to
  // check rather than a whole story surface. (`v7_startTransition` is a
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
