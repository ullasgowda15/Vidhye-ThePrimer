import { createBrowserRouter, Outlet } from 'react-router-dom';

import { ChildBoundary, ParentBoundary } from './components/SafeBoundary';
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
import PublicPrivacy from './routes/Privacy';
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
 *
 * **Each tree also has its own error boundary** (`components/SafeBoundary`, Day
 * 15). Not one shared boundary at the root, and the split is the point: a render
 * failure on the child surface must become the companion having a rest (§13 —
 * a child is never shown an error), and the same failure on the parent surface
 * must become a sentence a parent can act on (§12 — a parent is never lied to).
 * One boundary could only be one of those, and it would be the wrong one for
 * somebody. Wrapped at the *element* level rather than around the router so a
 * throw inside a page still leaves the surrounding route tree intact.
 */

/**
 * Wrap a child-tree element in the boundary that never shows a child an error.
 *
 * A helper because it goes on every child route, and a route that quietly did
 * not get it would be the only route in the app that can still white-screen a
 * four-year-old — invisible in a diff, and the exact failure this exists to
 * prevent.
 */
const forChild = (element: React.ReactElement) => <ChildBoundary>{element}</ChildBoundary>;

export const router = createBrowserRouter(
  [
    { path: '/', element: forChild(<ChildHome />) },
    // The imprint scene sits in the child tree, deliberately outside the parent
    // guards: the parent hands the tablet over here, and a child must never be
    // asked an adult challenge to meet their friend. It shows nothing an
    // unauthenticated request could obtain — without a session it stays a
    // sleeping companion.
    { path: '/meet/:childId', element: forChild(<MeetCompanion />) },
    // Coming back (§4.5). A sibling of `/meet` rather than a mode of it: the two
    // scenes answer different questions — "who is this?" and "do you remember
    // me?" — and the second is the one the whole retention thesis rests on. Same
    // place in the tree, outside the parent guards, for the same reason: the child
    // is holding the tablet.
    { path: '/welcome/:childId', element: forChild(<ReturningGreeting />) },
    // The story, in the child tree and outside the parent guards for the same
    // reason: the child is the one holding the tablet. It runs under the parent's
    // restored session; without one it stays a companion thinking, never a form.
    { path: '/story/:childId', element: forChild(<StoryPage />) },
    // The privacy statement, public and outside every guard (§12.8).
    //
    // It is not under `/parent` and that is the entire point: everything there is
    // behind `AdultGateRoute`, login included, so a statement mounted inside it
    // could only be read by someone who had already handed us a child's details.
    // The endpoint behind it has always been `AllowAny`; only the page was gated.
    //
    // `ParentBoundary` rather than `forChild`: the reader is an adult deciding
    // whether to trust us, so a failure here should be a sentence they can act on
    // rather than the companion having a rest. There is no adult challenge in
    // front of it — a challenge on a privacy policy would be a gate on the one
    // page that exists to be read before anyone commits to anything.
    {
      path: '/privacy',
      element: (
        <ParentBoundary>
          <PublicPrivacy />
        </ParentBoundary>
      ),
    },
    {
      path: '/parent',
      element: (
        <ParentBoundary>
          <Outlet />
        </ParentBoundary>
      ),
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
  // No `future` block any more: this is react-router **7**, where every one of
  // the v7_* behaviours this router used to opt into is simply how it works.
  // Opting in early on v6 is the reason the security upgrade (F-8) was a version
  // bump and two prop deletions rather than a routing migration in the middle of
  // a child-safety fix pass.
);
