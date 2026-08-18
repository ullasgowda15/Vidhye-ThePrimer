import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import PublicPrivacy from '@/routes/Privacy';
import { useAuthStore } from '@/store/auth';

/**
 * The public privacy statement (§12.8).
 *
 * The endpoint was always `AllowAny`; the only page rendering it lived under the
 * adult gate and a login, so the text was public and reading it was not. These
 * tests hold the property that closed that gap — and, more importantly, hold it
 * against the two ways it silently comes back: a guard added to the route, or an
 * authenticated request added to the page.
 */

const DISCLOSURE = {
  version: '2026-08-13',
  headline: 'What we collect, why, and what we will never do with it.',
  sections: [
    {
      id: 'never',
      title: 'What we never do',
      body: 'We never sell your data or your child’s.\n\nWe never train models on it.',
    },
    { id: 'retention', title: 'How long we keep it', body: 'For as long as you keep the account.' },
  ],
};

let fetchSpy: ReturnType<typeof vi.fn>;

function stub(status = 200, body: unknown = DISCLOSURE) {
  fetchSpy = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal('fetch', fetchSpy);
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/privacy']}>
        <PublicPrivacy />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Signed out, which is the whole point: this is the state a parent is in when
  // they are deciding whether to become a customer.
  useAuthStore.setState({ accessToken: null, status: 'anonymous', user: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the public privacy statement', () => {
  it('renders the whole statement to a signed-out visitor', async () => {
    stub();

    renderPage();

    expect(await screen.findByText('What we never do')).toBeInTheDocument();
    expect(screen.getByText('How long we keep it')).toBeInTheDocument();
    // Every section, not a teaser: a statement that stops before the part a
    // parent came for is a marketing page wearing a policy's name.
    expect(screen.getByText(/never sell your data/i)).toBeInTheDocument();
    expect(screen.getByText(/last changed on 2026-08-13/i)).toBeInTheDocument();
  });

  it('sends no credentials with the request', async () => {
    stub();

    renderPage();
    await screen.findByText('What we never do');

    // The security property, asserted at the wire rather than trusted from the
    // client helper: no Authorization header, and no cookies asked for. A page
    // that authenticated here would 401 the exact visitor it exists to serve.
    for (const call of fetchSpy.mock.calls) {
      const init = (call[1] ?? {}) as RequestInit;
      const headers = new Headers(init.headers ?? {});
      expect(headers.get('Authorization')).toBeNull();
      expect(init.credentials).not.toBe('include');
    }
  });

  it('asks only for the disclosure, and for nothing about a family', async () => {
    stub();

    renderPage();
    await screen.findByText('What we never do');

    const urls = fetchSpy.mock.calls.map((call) => String(call[0]));
    expect(urls.every((url) => url.includes('/privacy'))).toBe(true);
    for (const forbidden of ['/me', '/children', '/account', '/adventures']) {
      expect(urls.some((url) => url.includes(forbidden))).toBe(false);
    }
  });

  it('says so plainly when it cannot load, rather than showing nothing', async () => {
    stub(500, {});

    renderPage();

    // A blank page here reads as "they took the policy down". The heading is
    // static so the visitor still knows what they were promised, and the failure
    // is stated in a sentence rather than left as an empty column.
    await waitFor(() => expect(screen.getByText(/could not load this page/i)).toBeInTheDocument());
    expect(
      screen.getByRole('heading', { name: /what we do with your child/i }),
    ).toBeInTheDocument();
  });
});
