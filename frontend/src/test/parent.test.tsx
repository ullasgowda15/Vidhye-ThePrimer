/**
 * The parent surface's two gates and the onboarding flow (§14.2, §4.2).
 *
 * These render the real router, because what is being tested *is* the routing:
 * that a child tapping into `/parent` meets the adult challenge, and that an
 * unauthenticated visitor who gets past it meets a login form rather than an
 * empty dashboard.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Login from '@/routes/parent/Login';
import Onboarding from '@/routes/parent/Onboarding';
import AdultGateRoute from '@/routes/parent/AdultGateRoute';
import ParentGate from '@/routes/parent/ParentGate';
import ParentHome from '@/routes/parent/ParentHome';
import ProtectedRoute from '@/routes/parent/ProtectedRoute';
import { PARENT_GATE_TTL_MS, useAuthStore } from '@/store/auth';

function renderApp(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[initialPath]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={<p>storybook</p>} />
          <Route path="/parent/gate" element={<ParentGate />} />
          <Route path="/parent" element={<AdultGateRoute />}>
            <Route path="login" element={<Login />} />
            <Route path="onboarding" element={<Onboarding />} />
            <Route element={<ProtectedRoute />}>
              <Route index element={<ParentHome />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Solve the gate by reading the sum it actually rendered. */
async function passTheGate(user: ReturnType<typeof userEvent.setup>) {
  const input = await screen.findByLabelText(/what is \d+ times \d+\?/i);
  const [, a, b] = /what is (\d+) times (\d+)\?/i.exec(input.getAttribute('aria-label') ?? '')!;
  await user.type(input, String(Number(a) * Number(b)));
  await user.click(screen.getByRole('button', { name: 'Continue' }));
}

beforeEach(() => {
  // `anonymous`, not `loading`: these tests are about the guards' decisions, and
  // the bootstrap refresh that resolves `loading` has its own tests below.
  useAuthStore.setState({
    accessToken: null,
    status: 'anonymous',
    user: null,
    parentVerifiedUntil: null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('parent gate', () => {
  it('blocks entry to /parent until the adult challenge is answered', async () => {
    renderApp('/parent');

    expect(await screen.findByRole('heading', { name: 'Grown-ups only' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Parent area' })).not.toBeInTheDocument();
  });

  it('rejects a wrong answer and offers a different sum', async () => {
    const user = userEvent.setup();
    renderApp('/parent');

    const input = await screen.findByLabelText(/what is \d+ times \d+\?/i);
    const before = input.getAttribute('aria-label');
    await user.type(input, '1');
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(useAuthStore.getState().isParentVerified()).toBe(false);
    // A new challenge, so mashing the previous answer gets nowhere.
    await waitFor(() => {
      const after = screen.getByRole('textbox').getAttribute('aria-label');
      expect(after).not.toBe(before);
    });
  });

  it('lets an adult through, and the pass is short-lived', async () => {
    const user = userEvent.setup();
    renderApp('/parent');
    await passTheGate(user);

    // Through the gate but not signed in, so the login form is what waits.
    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument();

    const { parentVerifiedUntil } = useAuthStore.getState();
    expect(parentVerifiedUntil).toBeGreaterThan(Date.now());
    expect(parentVerifiedUntil).toBeLessThanOrEqual(Date.now() + PARENT_GATE_TTL_MS);
  });
});

describe('protected routes', () => {
  it('redirects an unauthenticated visitor to login', async () => {
    useAuthStore.setState({ parentVerifiedUntil: Date.now() + PARENT_GATE_TTL_MS });

    renderApp('/parent');

    expect(await screen.findByRole('heading', { name: 'Welcome back' })).toBeInTheDocument();
  });

  it('never flashes the login screen while the session is being restored', async () => {
    // The state every page load starts in: the gate has been passed in this tab,
    // the bootstrap refresh is in flight, and there is no token *yet*. A guard
    // that reads the token alone redirects here — and the parent sees the login
    // form appear and vanish on every single reload.
    useAuthStore.setState({
      parentVerifiedUntil: Date.now() + PARENT_GATE_TTL_MS,
      accessToken: null,
      status: 'loading',
    });

    renderApp('/parent');

    expect(await screen.findByTestId('session-restoring')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Welcome back' })).not.toBeInTheDocument();
  });

  it('shows the dashboard once a token is held', async () => {
    useAuthStore.setState({
      parentVerifiedUntil: Date.now() + PARENT_GATE_TTL_MS,
      accessToken: 'access-token',
      status: 'authenticated',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            id: 'p1',
            email: 'priya@example.com',
            display_name: 'Priya',
            locale: 'en-US',
            created_at: '2026-01-01T00:00:00Z',
            children: [],
          }),
      }),
    );

    renderApp('/parent');

    expect(await screen.findByRole('heading', { name: 'Parent area' })).toBeInTheDocument();
    expect(await screen.findByText('Priya')).toBeInTheDocument();
  });
});

describe('onboarding', () => {
  it('records consent before asking for anything about the child', async () => {
    const user = userEvent.setup();
    useAuthStore.setState({
      parentVerifiedUntil: Date.now() + PARENT_GATE_TTL_MS,
      accessToken: 'access-token',
      status: 'authenticated',
    });

    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init: RequestInit) => {
        calls.push(`${init.method} ${url}`);
        return Promise.resolve({
          ok: true,
          status: 201,
          text: async () => JSON.stringify({ id: 'x', granted_at: '2026-01-01T00:00:00Z' }),
        });
      }),
    );

    renderApp('/parent/onboarding');

    // The child form is not reachable until the attestation is ticked.
    expect(await screen.findByText(/Before we start/)).toBeInTheDocument();
    expect(screen.queryByLabelText('First name')).not.toBeInTheDocument();

    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /I agree/ }));

    await screen.findByText('Tell us about your child');

    await user.type(screen.getByLabelText('First name'), 'Aarav');
    await user.type(screen.getByLabelText('Birthday'), '2021-05-04');
    await user.type(screen.getByLabelText(/What does Aarav love\?/), 'dinosaurs, the sea');
    await user.type(screen.getByLabelText(/a little scared of\?/), 'the dark');
    await user.type(screen.getByLabelText(/avoid entirely\?/), 'hospitals');
    await user.click(screen.getByRole('button', { name: 'Finish' }));

    await waitFor(() => expect(calls).toContain('POST /api/v1/children'));

    // The consent call is first, and it is the only thing that happened before
    // the child was posted — the order §13.7 asks for, proven end to end.
    expect(calls.indexOf('POST /api/v1/consent')).toBe(0);
    expect(calls.indexOf('POST /api/v1/children')).toBe(1);
  });

  it('sends the intensity slider as an integer, never as its label', async () => {
    const user = userEvent.setup();
    useAuthStore.setState({
      parentVerifiedUntil: Date.now() + PARENT_GATE_TTL_MS,
      accessToken: 'access-token',
      status: 'authenticated',
    });

    const bodies: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_url: string, init: RequestInit) => {
        if (init.body) bodies.push(String(init.body));
        return Promise.resolve({ ok: true, status: 201, text: async () => '{}' });
      }),
    );

    renderApp('/parent/onboarding');
    await user.click(await screen.findByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /I agree/ }));

    await screen.findByText('Tell us about your child');
    await user.type(screen.getByLabelText('First name'), 'Aarav');
    await user.type(screen.getByLabelText('Birthday'), '2021-05-04');
    await user.click(screen.getByRole('button', { name: 'Finish' }));

    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(JSON.parse(bodies[1]).intensity_ceiling).toBe(2);
  });
});
