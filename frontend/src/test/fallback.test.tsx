/**
 * The safe-fallback rendering path (§13.2, §13.3, Day 7).
 *
 * **The whole design of this file is that there is nothing to test.** When the
 * backend's safety layer refuses a generation it serves a pre-vetted warm beat in
 * its place, and that beat arrives on the wire as an ordinary `beat` frame — same
 * event, same shape, same fields. So the correct client change for Day 7 is *no
 * change*: a fallback renders as a beat because it **is** a beat, and any code
 * here that special-cased one would be a way for a child to find out (§13).
 *
 * That is exactly why it needs a test. "We deliberately did nothing" is invisible
 * in a diff and is one well-meaning refactor from becoming a banner that says
 * "something went wrong". These assertions pin the absence: a fallback beat
 * renders narration, a picture slot, a question and tappable cards; the child can
 * answer it and the story goes on; and no state of this screen — fallback,
 * degraded media, or a stream that dies mid-beat — ever shows an error or a
 * spinner (§4.9, §14.5).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import StoryPage from '@/routes/child/StoryPage';
import { useAuthStore } from '@/store/auth';

/**
 * A beat as the backend's `safe_fallback_beat` actually produces it — copied
 * from `apps/safety/fallbacks.py`'s younger pool, personalised the way the
 * orchestrator personalises it.
 *
 * Note what is *not* in this payload, because that is the assertion: no `safety`
 * field, no `fallback: true`, no reason, no apology. The database knows
 * (`safety_status`, the `model` stamp, an incident row); the wire does not.
 */
const FALLBACK_BEAT = {
  beatId: 'beat-fallback',
  seq: 3,
  narration:
    'Pip stops still and points at the grass. A tiny snail is going somewhere, very slowly. Aarav, look how shiny its little shell is!',
  scene: { imageUrl: null, status: 'pending' },
  audioUrl: null,
  interaction: {
    type: 'choose',
    prompt_audio: 'Shall we wave to the snail, or watch it a bit longer?',
    expected: null,
    choices: [
      { key: 'wave_hello', label: 'Wave hello', icon_prompt: 'a waving hand' },
      { key: 'watch_it_go', label: 'Watch it go', icon_prompt: 'a snail shell on a leaf' },
    ],
  },
  choices: [
    { key: 'wave_hello', label: 'Wave hello', imageUrl: null },
    { key: 'watch_it_go', label: 'Watch it go', imageUrl: null },
  ],
  emotion: 'warm_calm',
  isFinal: false,
};

const ADVENTURE = {
  adventureId: 'adv-1',
  streamUrl: '/api/v1/adventures/adv-1/stream',
  companion: { name: 'Pip', voiceId: 'warm_low' },
  estimatedBeats: 6,
};

/** Words a child must never meet on this screen, in any state. */
const NEVER_SHOWN = [
  'error',
  'sorry',
  'oops',
  'something went wrong',
  'try again',
  'unavailable',
  'failed',
  'blocked',
  'fallback',
  'safety',
];

function frame(event: string, data: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status < 400,
    status,
    text: async () => (status === 204 ? '' : JSON.stringify(body)),
  });
}

function streamBody(chunks: string[]) {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[index++]));
    },
  });
}

function playFetch(opens: string[][]) {
  const posts: { url: string; body: unknown }[] = [];
  let open = 0;

  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = String(url);
    if (path.includes('/choice')) {
      posts.push({ url: path, body: init?.body ? JSON.parse(String(init.body)) : null });
      return jsonResponse(202, {
        choiceId: 'choice-1',
        beatId: FALLBACK_BEAT.beatId,
        recorded: true,
        streamUrl: ADVENTURE.streamUrl,
      });
    }
    if (path.includes('/adventures/current')) return jsonResponse(200, ADVENTURE);
    if (!path.includes('/stream')) return jsonResponse(201, ADVENTURE);

    const chunks = opens[Math.min(open, opens.length - 1)];
    open += 1;
    return Promise.resolve({ ok: true, status: 200, body: streamBody(chunks) });
  });

  return { fetchMock, posts };
}

const FALLBACK_OPEN = [
  ': open\n\n',
  frame('beat', FALLBACK_BEAT),
  frame('prefetch', { nextBeatReady: false, reason: 'branching' }),
  frame('done', { status: 'awaiting_choice' }),
];

function renderStory() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={['/story/child-1']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/story/:childId" element={<StoryPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const realRefresh = useAuthStore.getState().refresh;

beforeEach(() => {
  useAuthStore.setState({
    accessToken: 'access-token',
    status: 'authenticated',
    user: null,
    refresh: realRefresh,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('a blocked generation renders as a normal warm beat', () => {
  it('shows the narration and the question like any other beat', async () => {
    const { fetchMock } = playFetch([FALLBACK_OPEN]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    expect(await screen.findByTestId('beat-narration')).toHaveTextContent('A tiny snail');
    // The child's own name is in it. That is what stops a pre-vetted beat
    // reading as canned to the adult in the room.
    expect(screen.getByTestId('beat-narration')).toHaveTextContent('Aarav');
    expect(screen.getByTestId('beat-question')).toHaveTextContent('Shall we wave to the snail');
  });

  it('is answerable — the cards are there and they are tappable', async () => {
    const user = userEvent.setup();
    const { fetchMock, posts } = playFetch([FALLBACK_OPEN]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    const wave = await screen.findByRole('button', { name: /Wave hello/i });
    await user.click(wave);

    // The tap went back as an ordinary choice on an ordinary beat: nothing about
    // this beat is a dead end, which is what would make it an error in disguise.
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({
      beatId: FALLBACK_BEAT.beatId,
      choiceKey: 'wave_hello',
    });
  });

  it('says nothing anywhere on screen about anything having gone wrong', async () => {
    const { fetchMock } = playFetch([FALLBACK_OPEN]);
    vi.stubGlobal('fetch', fetchMock);

    const { container } = renderStory();
    await screen.findByTestId('beat-narration');

    const visible = (container.textContent ?? '').toLowerCase();
    for (const word of NEVER_SHOWN) {
      expect(visible).not.toContain(word);
    }
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows the companion thinking rather than a spinner while the picture lands', async () => {
    const { fetchMock } = playFetch([FALLBACK_OPEN]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await screen.findByTestId('beat-narration');

    // No illustration yet — a stock substitution arrives as a later `media`
    // frame exactly like a generated one. What stands in its place is a
    // character, not an indicator (§4.9).
    expect(screen.queryByTestId('beat-illustration')).toBeNull();
    // `getAllBy`: `SceneTransition` keeps the outgoing scene mounted through the
    // crossfade, so the thinking companion legitimately exists twice mid-turn.
    expect(screen.getAllByTestId('companion-thinking').length).toBeGreaterThan(0);
  });

  it('crossfades in the substituted illustration when it arrives', async () => {
    const { fetchMock } = playFetch([
      [
        ...FALLBACK_OPEN,
        // What the backend sends after `orchestrator._substitute_illustration`
        // has swapped a refused render for hand-drawn stock art. Indistinguishable
        // from a generated picture on the wire, and that is the design.
        frame('media', {
          beatId: FALLBACK_BEAT.beatId,
          scene: { imageUrl: '/media/assets/stock-meadow.svg', status: 'ready' },
        }),
      ],
    ]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    const picture = await screen.findByTestId('beat-illustration');
    expect(picture).toHaveAttribute('src', '/media/assets/stock-meadow.svg');
  });
});

describe('the screen still has no failure state', () => {
  it('keeps the beat on screen when the stream dies mid-flight', async () => {
    // No `done` frame at all: the connection dropped after the beat. The child
    // keeps the scene they are looking at and is never told why (§13).
    const { fetchMock } = playFetch([[': open\n\n', frame('beat', FALLBACK_BEAT)]]);
    vi.stubGlobal('fetch', fetchMock);

    const { container } = renderStory();
    await screen.findByTestId('beat-narration');

    const visible = (container.textContent ?? '').toLowerCase();
    for (const word of NEVER_SHOWN) {
      expect(visible).not.toContain(word);
    }
  });

  it('shows a line of story, not a loading message, before the first beat', async () => {
    const { fetchMock } = playFetch([[': open\n\n']]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    expect(await screen.findByTestId('story-thinking')).toHaveTextContent(
      /is thinking of somewhere to take you/,
    );
    expect(screen.queryByRole('progressbar')).toBeNull();
  });
});
