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
import { render, screen, waitFor } from '@testing-library/react';
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

/**
 * A `fetch` whose body arrives in the chunks we hand it, one per tick.
 *
 * Deliberately not one blob: the whole point of the endpoint is that frames land
 * separately, so a test that received them together would pass against a
 * buffering server.
 */
function streamingFetch(chunks: string[]) {
  const encoder = new TextEncoder();
  return vi.fn().mockImplementation((url: string) => {
    if (!String(url).includes('/stream')) {
      // The `POST /adventures` call that precedes the stream.
      return Promise.resolve({
        ok: true,
        status: 201,
        text: async () =>
          JSON.stringify({
            adventureId: 'adv-1',
            streamUrl: '/api/v1/adventures/adv-1/stream',
            companion: { name: 'Pip', voiceId: 'warm_low' },
            estimatedBeats: 6,
          }),
      });
    }
    let index = 0;
    return Promise.resolve({
      ok: true,
      status: 200,
      body: new ReadableStream<Uint8Array>({
        pull(controller) {
          if (index >= chunks.length) {
            controller.close();
            return;
          }
          controller.enqueue(encoder.encode(chunks[index++]));
        },
      }),
    });
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

beforeEach(() => {
  useAuthStore.setState({ accessToken: 'access-token', status: 'authenticated', user: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
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

    await consumeBeatStream('/api/v1/adventures/adv-1/stream', { accessToken: 'a', onEnd });

    // A dropped connection and a clean `done` look the same to the caller, which
    // is what keeps the child's screen a scene rather than an error.
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledWith(null);
  });

  it('carries the bearer token, because EventSource could not', async () => {
    const fetchMock = streamingFetch([frame('done', { status: 'awaiting_choice' })]);
    vi.stubGlobal('fetch', fetchMock);

    await consumeBeatStream('/api/v1/adventures/adv-1/stream', { accessToken: 'token-42' });

    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer token-42');
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
