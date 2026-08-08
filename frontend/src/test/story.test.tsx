/**
 * The story surface (§18.5, §14.5).
 *
 * Two things are worth testing here and they are both about *order*: the frame
 * parser has to survive chunk boundaries falling in unhelpful places, and the page
 * has to show narration before the picture rather than waiting for both.
 *
 * The third assertion is the one that matters most and is the easiest to lose in a
 * refactor: no spinner, at any point, in any state.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { consumeBeatStream, parseFrames } from '@/lib/sse';
import StoryPage from '@/routes/child/StoryPage';
import { useAuthStore } from '@/store/auth';

const BEAT = {
  beatId: 'beat-1',
  seq: 1,
  narration: 'Pip peeks over the log. Three little frogs!',
  scene: { imageUrl: null, status: 'pending' },
  audioUrl: null,
  interaction: {
    type: 'count',
    prompt_audio: 'How many frogs do you see?',
    expected: 3,
    choices: null,
  },
  emotion: 'playful_curious',
};

function frame(event: string, data: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const ADVENTURE = {
  adventureId: 'adv-1',
  streamUrl: '/api/v1/adventures/adv-1/stream',
  companion: { name: 'Pip', voiceId: 'warm_low' },
  estimatedBeats: 6,
};

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

/**
 * A `fetch` whose body arrives in the chunks we hand it, one per tick.
 *
 * Deliberately not one blob: the whole point of the endpoint is that frames land
 * separately, so a test that received them together would pass against a
 * buffering server.
 *
 * The non-stream branch answers `GET …/adventures/current` — the call the page
 * makes on mount so a reload rejoins the story in progress instead of paying for a
 * new one. `resume: false` makes it a 204 and puts the POST back in the path.
 */
function streamingFetch(chunks: string[], { resume = true }: { resume?: boolean } = {}) {
  return vi.fn().mockImplementation((url: string) => {
    const path = String(url);
    if (path.includes('/adventures/current')) {
      return resume ? jsonResponse(200, ADVENTURE) : jsonResponse(204, null);
    }
    if (!path.includes('/stream')) {
      // The `POST /adventures` call, reached only when there was nothing to resume.
      return jsonResponse(201, ADVENTURE);
    }
    return Promise.resolve({ ok: true, status: 200, body: streamBody(chunks) });
  });
}

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

/** The store's real `refresh`, so the one test that replaces it cannot leak. */
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
  // One test spies on `HTMLMediaElement.prototype.play`, which is shared across
  // the file — a leaked spy would silently change a later test.
  vi.restoreAllMocks();
});

describe('frame parser', () => {
  it('reassembles a frame split across chunks', async () => {
    const whole = frame('beat', BEAT);
    const cut = Math.floor(whole.length / 2);

    const first = parseFrames(whole.slice(0, cut));
    expect(first.frames).toHaveLength(0);

    const second = parseFrames(first.rest + whole.slice(cut));
    expect(second.frames).toHaveLength(1);
    expect(JSON.parse(second.frames[0].data).beatId).toBe('beat-1');
  });

  it('ignores the comment the server sends to flush headers', () => {
    const { frames } = parseFrames(`: open\n\n${frame('done', { status: 'awaiting_choice' })}`);
    expect(frames).toHaveLength(1);
    expect(frames[0].event).toBe('done');
  });

  it('reports the stream ending even when it just closes', async () => {
    vi.stubGlobal('fetch', streamingFetch([': open\n\n']));
    const onEnd = vi.fn();

    await consumeBeatStream('/api/v1/adventures/adv-1/stream', { onEnd });

    // A dropped connection and a clean `done` look the same to the caller, which
    // is what keeps the child's screen a scene rather than an error.
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledWith(null);
  });

  it('carries the bearer token, because EventSource could not', async () => {
    useAuthStore.setState({ accessToken: 'token-42' });
    const fetchMock = streamingFetch([frame('done', { status: 'awaiting_choice' })]);
    vi.stubGlobal('fetch', fetchMock);

    await consumeBeatStream('/api/v1/adventures/adv-1/stream', {});

    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer token-42');
    expect(fetchMock.mock.calls[0][1].headers.Accept).toBe('text/event-stream');
  });

  it('refreshes and retries once when the token expired before the stream opened', async () => {
    // The child's session outlives a fifteen-minute access token, so this is
    // routine rather than exotic. Before the stream went through the API client it
    // was the one authenticated call with no silent refresh, and a 401 here is a
    // companion who thinks forever — the child has no way to fix it and no adult
    // to tell.
    const refresh = vi.fn().mockResolvedValue('fresh-token');
    useAuthStore.setState({ accessToken: 'stale-token', refresh });

    const opened = streamingFetch([
      frame('beat', BEAT),
      frame('done', { status: 'awaiting_choice' }),
    ]);
    let expired = true;
    const fetchMock = vi.fn().mockImplementation((url: string, init: RequestInit) => {
      if (expired) {
        expired = false;
        return Promise.resolve({ ok: false, status: 401, text: async () => '{}' });
      }
      return opened(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);

    const onBeat = vi.fn();
    await consumeBeatStream('/api/v1/adventures/adv-1/stream', { onBeat });

    expect(refresh).toHaveBeenCalledTimes(1);
    // The retry carried the new token, and the beat actually arrived — the story
    // continues rather than stalling on the thinking scene.
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer fresh-token');
    expect(onBeat).toHaveBeenCalledTimes(1);
  });
});

describe('StoryPage', () => {
  it('renders narration first, then swaps in the illustration', async () => {
    vi.stubGlobal(
      'fetch',
      streamingFetch([
        ': open\n\n',
        frame('beat', BEAT),
        frame('media', {
          beatId: 'beat-1',
          scene: { imageUrl: '/media/frogs.png', status: 'ready' },
        }),
        frame('media', { beatId: 'beat-1', audioUrl: '/media/frogs.mp3' }),
        frame('done', { status: 'awaiting_choice' }),
      ]),
    );

    renderStory();

    // The narration is on screen before any picture exists. This ordering is the
    // reason the endpoint streams at all (§18.5).
    const narration = await screen.findByTestId('beat-narration');
    expect(narration).toHaveTextContent('Three little frogs');
    expect(screen.getByTestId('beat-question')).toHaveTextContent('How many frogs');

    await waitFor(() =>
      expect(screen.getByTestId('beat-illustration')).toHaveAttribute('src', '/media/frogs.png'),
    );
    await waitFor(() =>
      expect(screen.getByTestId('beat-audio')).toHaveAttribute('src', '/media/frogs.mp3'),
    );
  });

  it('rejoins the story in progress instead of starting a new one', async () => {
    // The reload leak. React Query's cache is memory-only, so a hard refresh forgot
    // the adventure id and the page POSTed again — a plan, an opening beat, an
    // illustration and a voice line, every reload. The founder's dev database grew
    // by one adventure per refresh.
    const fetchMock = streamingFetch([': open\n\n', frame('beat', BEAT)]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await screen.findByTestId('beat-narration');

    const methods = fetchMock.mock.calls.map(([url, init]) => [String(url), init?.method ?? 'GET']);
    expect(methods.some(([url]) => url.includes('/adventures/current'))).toBe(true);
    expect(methods.some(([, method]) => method === 'POST')).toBe(false);
  });

  it('starts one when the child is not in a story yet', async () => {
    // The other half: a 204 means there is nothing to rejoin, and only then does
    // anything get generated.
    const fetchMock = streamingFetch([': open\n\n', frame('beat', BEAT)], { resume: false });
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await screen.findByTestId('beat-narration');

    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(String(posts[0][0])).toContain('/adventures');
  });

  it('shows the companion thinking, never a spinner, while the beat generates', async () => {
    // A stream that opens and says nothing else — the several seconds a real
    // generation takes, frozen.
    vi.stubGlobal('fetch', streamingFetch([': open\n\n']));

    renderStory();

    expect(await screen.findByTestId('companion-thinking')).toBeInTheDocument();
    expect(screen.getByTestId('story-thinking')).toHaveTextContent('is thinking');

    // The rule this whole screen is built around (§14.5). A four-year-old cannot
    // read a status and cannot interpret a rotating arc.
    expect(document.body.textContent).not.toMatch(/loading|please wait|error|failed/i);
    expect(document.querySelector('[role="progressbar"]')).toBeNull();
  });

  it('does not caption the question twice when a beat repeats itself', async () => {
    // What reached the child: the provider filled `prompt_audio` with the whole
    // narration, so the same sentences were on the page twice — and, because both
    // go into one clip, said twice in a row.
    const repeated = {
      ...BEAT,
      interaction: { ...BEAT.interaction, prompt_audio: BEAT.narration },
    };
    vi.stubGlobal('fetch', streamingFetch([': open\n\n', frame('beat', repeated)]));

    renderStory();

    const narration = await screen.findByTestId('beat-narration');
    expect(narration).toHaveTextContent('Three little frogs');
    expect(screen.queryByTestId('beat-question')).toBeNull();
  });

  it('still captions a question the narration has not already asked', async () => {
    // The guard must not eat a real question: a child asked nothing is worse than
    // one asked clumsily.
    vi.stubGlobal('fetch', streamingFetch([': open\n\n', frame('beat', BEAT)]));

    renderStory();

    expect(await screen.findByTestId('beat-question')).toHaveTextContent('How many frogs');
  });

  it('does not start the story over when the child touches the screen', async () => {
    const user = userEvent.setup();
    const play = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockImplementation(play);
    vi.stubGlobal(
      'fetch',
      streamingFetch([
        ': open\n\n',
        frame('beat', BEAT),
        frame('media', { beatId: 'beat-1', audioUrl: '/media/frogs.mp3' }),
      ]),
    );

    renderStory();

    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    fireEvent.ended(screen.getByTestId('beat-audio'));

    // A child explores a screen by touching it. While this handler stayed armed,
    // every touch restarted the narration from the first line.
    await user.click(screen.getByTestId('beat-narration'));
    expect(play).toHaveBeenCalledTimes(1);

    // Told again on request, though — that is a reasonable thing to want.
    await user.click(screen.getByTestId('beat-replay'));
    expect(play).toHaveBeenCalledTimes(2);
  });

  it('stays a scene when the stream ends with no beat at all', async () => {
    // The unrepairable-generation path: the server ends the stream gracefully and
    // the child is left looking at their friend, still thinking (§13).
    vi.stubGlobal('fetch', streamingFetch([': open\n\n', frame('done', { status: 'ended' })]));

    renderStory();

    expect(await screen.findByTestId('companion-thinking')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('beat-narration')).toBeNull());
    expect(document.body.textContent).not.toMatch(/error|sorry|failed|try again/i);
  });
});
