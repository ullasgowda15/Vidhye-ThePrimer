/**
 * Coming back — the day-two scene (PRODUCT.md §4.5, §13).
 *
 * Three product rules, not rendering details:
 *
 * 1. A returning child is told they are **welcome back** and shown what the
 *    companion remembers. A new child is not — "welcome back" said to a child on
 *    their first day is the one thing this screen must never do, so the heading is
 *    driven by `kind` from the server and never inferred from the text.
 * 2. The scene **never shows a spinner or an error**. A greeting that is still
 *    being written is a friend walking over; a request that fails outright is a
 *    friend who is simply there.
 * 3. It is asked **once**. This is the one read on the child surface that costs a
 *    language call and a voice call, so a scene that polled it would generate a
 *    second hello for a child already listening to the first.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ChildHome from '@/routes/child/ChildHome';
import ReturningGreeting from '@/routes/child/ReturningGreeting';
import { useAuthStore } from '@/store/auth';

const COMPANION = {
  id: 'c1',
  child: 'child-1',
  name: 'Sparkle',
  archetype: 'dragon',
  palette: 'ember',
  assets_ready: true,
  assets_status: 'ready',
  portrait_url: '/media/assets/child-1/portrait.svg',
  expressions: {},
  greeting: { text: "Hi Rina! I'm Sparkle.", audio_url: '/media/assets/child-1/hello.wav' },
};

const RETURNING = {
  kind: 'returning',
  text: 'Rina! You came back. I keep thinking about those three stepping stones you counted.',
  audio_url: '/media/assets/child-1/welcome-back.wav',
  companion_name: 'Sparkle',
  remembered: 'Rina counted three stepping stones across the stream.',
};

const FIRST_MEETING = {
  kind: 'first_meeting',
  text: "Hi Rina! I'm Sparkle. I've been waiting to meet you.",
  audio_url: '/media/assets/child-1/hello.wav',
  companion_name: 'Sparkle',
  remembered: '',
};

/** A fetch stub that answers by URL and records every call. */
function stubApi(routes: Record<string, unknown>, calls: string[] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      calls.push(url);
      const match = Object.keys(routes).find((key) => url.includes(key));
      if (!match) return Promise.resolve({ ok: false, status: 404, text: async () => '{}' });
      const body = routes[match];
      if (body === 'fail') {
        return Promise.resolve({ ok: false, status: 500, text: async () => '{}' });
      }
      return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(body) });
    }),
  );
  return calls;
}

function renderWith(ui: React.ReactElement, path = '/welcome/child-1') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={ui} />
          <Route path="/welcome/:childId" element={ui} />
          <Route path="/story/:childId" element={<div data-testid="story-route">story</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: 'access-token', status: 'authenticated', user: null });
  // jsdom has no media pipeline; `play()` is not implemented at all.
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the returning greeting', () => {
  it('welcomes the child back and shows what the companion remembers', async () => {
    stubApi({ greeting: RETURNING, companion: COMPANION });

    renderWith(<ReturningGreeting />);

    expect(await screen.findByTestId('greeting-heading')).toHaveTextContent('Welcome back');
    // The greeting names the child and names the thing they actually did — the
    // whole of §4.5, on screen.
    expect(screen.getByTestId('greeting-text')).toHaveTextContent('Rina');
    expect(screen.getByTestId('greeting-text')).toHaveTextContent('stepping stones');
    expect(screen.getByTestId('greeting-remembered')).toHaveTextContent('stepping stones');
  });

  it('speaks it aloud without waiting to be asked', async () => {
    stubApi({ greeting: RETURNING, companion: COMPANION });

    renderWith(<ReturningGreeting />);

    await screen.findByTestId('greeting-text');
    await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalled());
  });

  it('opens today’s story when the star is tapped', async () => {
    const user = userEvent.setup();
    stubApi({ greeting: RETURNING, companion: COMPANION });

    renderWith(<ReturningGreeting />);

    await user.click(await screen.findByTestId('begin-story'));
    expect(screen.getByTestId('story-route')).toBeInTheDocument();
  });

  it('never says "welcome back" to a child on their first day', async () => {
    stubApi({ greeting: FIRST_MEETING, companion: COMPANION });

    renderWith(<ReturningGreeting />);

    const heading = await screen.findByTestId('greeting-heading');
    expect(heading).not.toHaveTextContent('Welcome back');
    expect(heading).toHaveTextContent('Sparkle');
    // Nothing is remembered yet, and the scene does not pretend otherwise.
    expect(screen.queryByTestId('greeting-remembered')).not.toBeInTheDocument();
    expect(screen.getByTestId('greeting-text')).toHaveTextContent('waiting to meet you');
  });

  it('narrates the wait instead of showing a spinner', () => {
    // Nothing resolves: the greeting is still being written.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => new Promise(() => {})),
    );

    renderWith(<ReturningGreeting />);

    expect(screen.getByTestId('greeting-arriving')).toHaveTextContent('coming to say hello');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('still lets the child reach their story when the request fails', async () => {
    const user = userEvent.setup();
    stubApi({ greeting: 'fail', companion: COMPANION });

    renderWith(<ReturningGreeting />);

    // No error to read, and — the part that matters — no dead end. Stranding a
    // four-year-old on a scene with nothing to tap is worse than showing them a
    // message they cannot read: they lose the hello, not the evening (§13, §4.9).
    expect(await screen.findByTestId('companion-portrait')).toBeInTheDocument();
    expect(screen.queryByText(/error|failed|sorry|wrong/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId('greeting-arriving')).not.toBeInTheDocument();

    await user.click(await screen.findByTestId('begin-story'));
    expect(screen.getByTestId('story-route')).toBeInTheDocument();
  });

  it('is asked once per arrival and never polled', async () => {
    const calls = stubApi({ greeting: RETURNING, companion: COMPANION });

    renderWith(<ReturningGreeting />);
    await screen.findByTestId('greeting-text');

    // A second render of the same key must not buy a second greeting.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((url) => url.includes('/greeting'))).toHaveLength(1);
  });
});

describe('the way in', () => {
  it('sends the star to the welcome scene, not straight to the story', async () => {
    const user = userEvent.setup();
    stubApi({
      health: { status: 'ok', service: 'primer', version: '0' },
      me: { id: 'p1', email: 'a@b.c', children: [{ id: 'child-1', first_name: 'Rina' }] },
      greeting: RETURNING,
      companion: COMPANION,
    });

    // Its own route table rather than `renderWith`'s: this is the one test about
    // *which* scene the star reaches, so the two destinations have to be
    // distinguishable rather than both being the component under test.
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route path="/" element={<ChildHome />} />
            <Route path="/welcome/:childId" element={<ReturningGreeting />} />
            <Route path="/story/:childId" element={<div data-testid="story-route">story</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(await screen.findByTestId('begin-story'));

    // The welcome scene, which is where §4.5 happens — the story is one tap on.
    expect(await screen.findByTestId('greeting-heading')).toHaveTextContent('Welcome back');
    expect(screen.queryByTestId('story-route')).not.toBeInTheDocument();
  });
});
