import { createBrowserRouter } from 'react-router-dom';

import ChildHome from './routes/child/ChildHome';
import MeetCompanion from './routes/child/MeetCompanion';
import ReturningGreeting from './routes/child/ReturningGreeting';
import StoryPage from './routes/child/StoryPage';
import AdultGateRoute from './routes/parent/AdultGateRoute';
import Children from './routes/parent/Children';
import Home from './routes/parent/Home';
import Insights from './routes/parent/Insights';
import Login from './routes/parent/Login';
import Onboarding from './routes/parent/Onboarding';
import ParentPrivacy from './routes/parent/Privacy';
import ParentGate from './routes/parent/ParentGate';
import ProtectedRoute from './routes/parent/ProtectedRoute';
import Reports from './routes/parent/Reports';
import SessionReport from './routes/parent/SessionReport';
import Settings from './routes/parent/Settings';
import Signup from './routes/parent/Signup';
import Transparency from './routes/parent/Transparency';
import WeeklyReport from './routes/parent/WeeklyReport';

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
    // Coming back (§4.5). A sibling of `/meet` rather than a mode of it: the two
    // scenes answer different questions — "who is this?" and "do you remember
    // me?" — and the second is the one the whole retention thesis rests on. Same
    // place in the tree, outside the parent guards, for the same reason: the child
    // is holding the tablet.
    { path: '/welcome/:childId', element: <ReturningGreeting /> },
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
              // The dashboard (§12.10). `child/:childId/...` rather than
              // `:childId/...` so a child id can never be confused with a
              // sibling route like `login` — React Router ranks static segments
              // above dynamic ones, but a URL space where those two kinds of
              // segment share a level is one rename away from a collision.
              children: [
                { index: true, element: <Home /> },
                { path: 'children', element: <Children /> },
                { path: 'child/:childId', element: <Reports /> },
                { path: 'child/:childId/reports', element: <Reports /> },
                { path: 'child/:childId/reports/:sessionId', element: <SessionReport /> },
                { path: 'child/:childId/weekly', element: <WeeklyReport /> },
                { path: 'child/:childId/insights', element: <Insights /> },
                { path: 'child/:childId/transparency', element: <Transparency /> },
                { path: 'child/:childId/settings', element: <Settings /> },
                // The privacy centre is per-account rather than per-child: export
                // and account deletion span every child, and a page that lived
                // under one child would imply otherwise. Deleting a *single*
                // child is on it, listed by name.
                { path: 'privacy', element: <ParentPrivacy /> },
                {
                  path: 'child/:childId/transparency/:adventureId',
                  element: <Transparency />,
                },
              ],
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
