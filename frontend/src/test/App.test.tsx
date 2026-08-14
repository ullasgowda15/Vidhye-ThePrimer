import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { tokens } from '@/design/tokens';
import { queryKeys } from '@/lib/api/queries';
import ChildHome from '@/routes/child/ChildHome';
import ParentHome from '@/routes/parent/Home';
import { useAuthStore } from '@/store/auth';

function renderWithProviders(ui: React.ReactElement, initialPath = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // `useMe` only fires for a signed-in parent, and the whole point of the front
  // door is that it behaves differently for a family than for a stranger — so a
  // test that never authenticated would silently be testing the visitor path
  // every time, including the ones asserting the star.
  useAuthStore.setState({ accessToken: 'access-token', status: 'authenticated', user: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.setState({ accessToken: null, status: 'anonymous', user: null });
});

function stubApi(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      const match = Object.keys(routes).find((key) => url.includes(key));
      if (!match) return Promise.resolve({ ok: false, status: 404, text: async () => '{}' });
      if (routes[match] === 'fail') {
        return Promise.resolve({ ok: false, status: 500, text: async () => '{}' });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(routes[match]),
      });
    }),
  );
}

const HEALTHY = { status: 'ok', service: 'primer', version: '0.1.0' };
const ONE_CHILD = { id: 'p1', email: 'a@b.c', children: [{ id: 'child-1', first_name: 'Rina' }] };
const TWO_CHILDREN = {
  id: 'p1',
  email: 'a@b.c',
  children: [
    { id: 'child-1', first_name: 'Rina' },
    { id: 'child-2', first_name: 'Bo' },
  ],
};

/**
 * Two audiences arrive at the front door, and they ask different questions.
 *
 * A child holding a tablet their parent set up wants the star. A stranger wants
 * to know what this is and whether to trust it with a four-year-old. They used to
 * get the same page — the storybook, with no answer to the second question — so
 * the route now chooses, and both halves are pinned here.
 */
describe('the front door', () => {
  it('shows a visitor with no child profile what the product is', async () => {
    stubApi({ health: HEALTHY, '/me': { id: 'p1', email: 'a@b.c', children: [] } });

    renderWithProviders(<ChildHome />);

    expect(
      await screen.findByRole('heading', { name: /a friend who remembers your child/i }),
    ).toBeInTheDocument();
    // And never the child's doorway: a stranger tapping a star would land in
    // somebody's story with no idea what they had opened.
    expect(screen.queryByTestId('begin-story')).toBeNull();
  });

  it('shows a family the star, not the sales page', async () => {
    stubApi({ health: HEALTHY, '/me': ONE_CHILD });

    renderWithProviders(<ChildHome />);

    expect(await screen.findByTestId('begin-story')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The Primer' })).toBeInTheDocument();
    expect(screen.queryByText(/start a storybook/i)).toBeNull();
  });

  it('shows no child once the parent signs out, even with a warm cache', () => {
    // The defect: `useMe` is disabled without a token, and a disabled query still
    // hands back whatever it fetched last — so after signing out the front door
    // went on drawing a star per child *with their first names on it*, for
    // whoever picked the tablet up next. A child's name on a signed-out screen is
    // §13.7's problem, not a routing one.
    //
    // The cache is seeded and the token removed, which is exactly the state a
    // sign-out used to leave behind: `useAuthStore.logout` cleared the token and
    // nothing cleared the cache. `useLogout` now does both; this asserts the page
    // is safe even if some future caller forgets it again.
    stubApi({ health: HEALTHY, '/me': TWO_CHILDREN });
    useAuthStore.setState({ accessToken: null, status: 'anonymous', user: null });

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(queryKeys.me, TWO_CHILDREN);

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <ChildHome />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.queryByText('Rina')).toBeNull();
    expect(screen.queryByText('Bo')).toBeNull();
    expect(screen.queryByTestId('begin-story-child-1')).toBeNull();
    expect(
      screen.getByRole('heading', { name: /a friend who remembers your child/i }),
    ).toBeInTheDocument();
  });

  it('gives every sibling their own star', async () => {
    // The defect this replaced: the front door opened the first profile and nothing
    // else, so a second child could not reach their own story from it at all.
    stubApi({ health: HEALTHY, '/me': TWO_CHILDREN });

    renderWithProviders(<ChildHome />);

    expect(await screen.findByTestId('begin-story-child-1')).toBeInTheDocument();
    expect(screen.getByTestId('begin-story-child-2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /begin Bo's story/i })).toBeInTheDocument();
    // No single anonymous star, which is what made the old one ambiguous.
    expect(screen.queryByTestId('begin-story')).toBeNull();
  });
});

describe('child route', () => {
  it('shows the health status once the API answers', async () => {
    stubApi({ health: HEALTHY, '/me': ONE_CHILD });

    renderWithProviders(<ChildHome />);

    expect(await screen.findByText('the story is awake')).toBeInTheDocument();
  });

  it('never surfaces a raw error to the child when the API is down', async () => {
    stubApi({ health: 'fail', '/me': ONE_CHILD });

    renderWithProviders(<ChildHome />);

    // The story shell stays up; only the quiet dot changes.
    expect(await screen.findByTestId('begin-story')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The Primer' })).toBeInTheDocument();

    // §14.5, and the Day 1-15 audit's F-9: this used to assert the word
    // 'offline', three lines below a docstring promising the child no error
    // state. A failed health check is now indistinguishable, on this screen,
    // from a slow one — both say the same thing, in story words.
    expect(await screen.findByText('the story is dreaming')).toBeInTheDocument();
    for (const word of ['offline', 'error', 'failed', 'network', 'unavailable']) {
      expect(screen.queryByText(new RegExp(word, 'i'))).toBeNull();
    }
  });
});

describe('parent route', () => {
  it('renders under the Calm theme', () => {
    const { container } = renderWithProviders(<ParentHome />, '/parent');

    expect(screen.getByRole('heading', { name: 'Parent area' })).toBeInTheDocument();
    // The theme switch is a data attribute, not a component branch (§15.11).
    expect(container.querySelector('[data-theme="calm"]')).not.toBeNull();
  });
});

describe('design tokens', () => {
  it('exposes the semantic layer rather than raw values', () => {
    expect(tokens.color.primary).toBe('var(--primary)');
    expect(tokens.space[4]).toBe('var(--space-4)');
    expect(tokens.motion.base).toBe('var(--motion-base)');
  });
});
