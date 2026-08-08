/**
 * The story surface (§8.5, §8.8, §14.4, §14.5, §18.5).
 *
 * Two things here are about *order*: the frame parser has to survive chunk
 * boundaries falling in unhelpful places, and the page has to show narration
 * before the picture rather than waiting for both.
 *
 * The rest is about the loop, and every one of those tests is really the same
 * assertion from a different angle — **nothing on this screen can go wrong.** No
 * spinner in any state; a card that renders before its picture exists; a count the
 * child misses that produces help rather than a mark; a failed request the child
 * is never told about; an ending with no score on it. Each is easy to lose in a
 * refactor and each would be felt by a four-year-old before anyone else noticed.
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
  choices: null,
  emotion: 'playful_curious',
  isFinal: false,
};

/** A decision point: two illustrated options, neither of which can lose (§8.5). */
const DECISION = {
  ...BEAT,
  beatId: 'beat-2',
  seq: 2,
  narration: 'The chorus is missing a singer.',
  interaction: {
    type: 'choose',
    prompt_audio: 'Which way shall we go?',
    expected: null,
    choices: [
      { key: 'follow_river', label: 'Follow the river', icon_prompt: 'a river' },
      { key: 'ask_dragonflies', label: 'Ask the dragonflies', icon_prompt: 'dragonflies' },
    ],
  },
  choices: [
    { key: 'follow_river', label: 'Follow the river', imageUrl: null },
    { key: 'ask_dragonflies', label: 'Ask the dragonflies', imageUrl: null },
  ],
};

/** The warm close (§8.8): nothing to answer, and a reason to come back. */
const CLOSE = {
  ...BEAT,
  beatId: 'beat-6',
  seq: 6,
  narration: "Pip yawns. 'Come back tomorrow — I found a map to somewhere new.'",
  interaction: { type: 'none', prompt_audio: null, expected: null, choices: null },
  choices: null,
  isFinal: true,
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

/**
 * A `fetch` that plays a *sequence* of stream opens, one per connection.
 *
 * This is the shape the loop actually has: one open delivers one beat, the child
 * acts, and the client reconnects for the consequence (§18.6). A mock that
 * replayed the same body every time could not tell "reopened for the next beat"
 * from "reopened and got the same beat again", which is the distinction every
 * test below turns on. The choice and end POSTs are recorded so the request the
 * child's tap produced can be asserted directly.
 */
function playFetch(opens: string[][]) {
  const posts: { url: string; body: unknown }[] = [];
  let open = 0;

  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const path = String(url);
    if (path.includes('/choice') || path.includes('/end')) {
      posts.push({ url: path, body: init?.body ? JSON.parse(String(init.body)) : null });
      return jsonResponse(path.includes('/end') ? 200 : 202, {
        choiceId: 'choice-1',
        beatId: 'beat-1',
        recorded: true,
        streamUrl: ADVENTURE.streamUrl,
        adventureId: ADVENTURE.adventureId,
        status: 'completed',
        completedAt: '2026-08-08T12:00:00Z',
        beats: 6,
      });
    }
    if (path.includes('/adventures/current')) return jsonResponse(200, ADVENTURE);
    if (!path.includes('/stream')) return jsonResponse(201, ADVENTURE);

    const chunks = opens[Math.min(open, opens.length - 1)];
    open += 1;
    return Promise.resolve({ ok: true, status: 200, body: streamBody(chunks) });
  });

  return { fetchMock, posts, openCount: () => open };
}

/** One open that delivers the decision beat and hands the turn to the child. */
const DECISION_OPEN = [
  ': open\n\n',
  frame('beat', DECISION),
  frame('prefetch', { nextBeatReady: false }),
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

/**
 * The loop (§8.5, §14.4, §14.5). What is under test here is the *whole* turn: a
 * decision beat with pictures on it, the tap, the request that tap produces, and
 * the consequence arriving without the screen ever going blank.
 */
describe('choices', () => {
  const DECISION_OPEN = [
    ': open\n\n',
    frame('beat', DECISION),
    frame('prefetch', { nextBeatReady: false }),
    frame('done', { status: 'awaiting_choice' }),
  ];

  it('offers illustrated cards rather than a list of words', async () => {
    const { fetchMock } = playFetch([
      [
        ': open\n\n',
        frame('beat', DECISION),
        frame('media', {
          beatId: 'beat-2',
          choice: { key: 'follow_river', imageUrl: '/media/river.png' },
        }),
        frame('done', { status: 'awaiting_choice' }),
      ],
    ]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    // Both cards are on screen before either picture is; a card that waited for
    // its icon would be a choice a pre-reader could not take (§14.4).
    const cards = await screen.findByTestId('choice-cards');
    expect(cards).toBeInTheDocument();
    expect(screen.getByTestId('choice-ask_dragonflies')).toBeInTheDocument();
    expect(screen.getByTestId('choice-placeholder-ask_dragonflies')).toBeInTheDocument();

    // The picture drops into the card that is already there.
    await waitFor(() =>
      expect(screen.getByTestId('choice-image-follow_river')).toHaveAttribute(
        'src',
        '/media/river.png',
      ),
    );
    // Labels are captions for the adult and the screen reader; the child hears
    // them in the same clip as the narration.
    expect(screen.getByTestId('choice-follow_river')).toHaveAccessibleName('Follow the river');
  });

  it('keeps filling the cards after the child has been given their turn', async () => {
    // `done` means "the child may act now", and the server sends it as soon as the
    // beat's own picture and voice are in — a card without its icon is still a
    // card, but a child cannot act without the question (§14.5). So the icons
    // arrive *after* `done`, and hanging up on it would leave a decision point
    // permanently wearing placeholders.
    const { fetchMock } = playFetch([
      [
        ': open\n\n',
        frame('beat', DECISION),
        frame('done', { status: 'awaiting_choice' }),
        frame('media', {
          beatId: 'beat-2',
          choice: { key: 'ask_dragonflies', imageUrl: '/media/dragonflies.png' },
        }),
      ],
    ]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    // The cards are tappable first…
    await screen.findByTestId('choice-cards');
    // …and the picture drops in afterwards.
    await waitFor(() =>
      expect(screen.getByTestId('choice-image-ask_dragonflies')).toHaveAttribute(
        'src',
        '/media/dragonflies.png',
      ),
    );
  });

  it('sends the choice with its evidence and streams the consequence', async () => {
    const user = userEvent.setup();
    const consequence = {
      ...BEAT,
      beatId: 'beat-3',
      seq: 3,
      narration: 'Because you chose the river, the frogs lead you onward!',
    };
    const { fetchMock, posts } = playFetch([
      DECISION_OPEN,
      [': open\n\n', frame('beat', consequence), frame('done', { status: 'awaiting_choice' })],
    ]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await user.click(await screen.findByTestId('choice-follow_river'));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain('/adventures/adv-1/choice');
    expect(posts[0].body).toMatchObject({
      beatId: 'beat-2',
      choiceKey: 'follow_river',
      retries: 0,
      assisted: false,
    });
    // Every interaction carries a latency, because Day 10 reads it as evidence.
    expect((posts[0].body as { latencyMs: number }).latencyMs).toBeGreaterThanOrEqual(0);

    // And the story moved on.
    await waitFor(() =>
      expect(screen.getByTestId('beat-narration')).toHaveTextContent('Because you chose the river'),
    );
  });

  it('keeps the current scene on screen while the consequence generates', async () => {
    // The transition is the latency-hider (§4.9). Blanking the page back to the
    // thinking companion between every beat would be a spinner in costume.
    const user = userEvent.setup();
    const { fetchMock } = playFetch([
      [
        ': open\n\n',
        frame('beat', DECISION),
        frame('media', {
          beatId: 'beat-2',
          scene: { imageUrl: '/media/log.png', status: 'ready' },
        }),
        frame('done', { status: 'awaiting_choice' }),
      ],
      // The next open says nothing at all — the several seconds a real generation
      // takes, frozen.
      [': open\n\n'],
    ]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await user.click(await screen.findByTestId('choice-follow_river'));

    // The tap is acknowledged on the card itself (§14.7) — it stays up, marked as
    // the one that was picked. A row that vanished on touch would be a screen that
    // swallowed the tap.
    await waitFor(() =>
      expect(screen.getByTestId('choice-follow_river')).toHaveAttribute('data-picked'),
    );
    expect(screen.getByTestId('choice-ask_dragonflies')).not.toHaveAttribute('data-picked');

    // And the scene the child just changed is still there, picture and all.
    expect(screen.getByTestId('beat-narration')).toHaveTextContent('missing a singer');
    expect(screen.getByTestId('beat-illustration')).toHaveAttribute('src', '/media/log.png');
    expect(screen.queryByTestId('companion-thinking')).toBeNull();
  });

  it('cannot be answered twice by a double tap', async () => {
    const user = userEvent.setup();
    const { fetchMock, posts } = playFetch([DECISION_OPEN, [': open\n\n']]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    const card = await screen.findByTestId('choice-follow_river');
    await user.click(card);
    await user.click(card);

    // One POST. The server would also settle it, but a child double-tapping a big
    // forgiving target is the normal case and should not depend on that.
    await waitFor(() => expect(posts).toHaveLength(1));
  });
});

/**
 * §8.5, made visible: a child cannot get this wrong, and the companion helps.
 */
describe('no fail states', () => {
  it('offers a hand instead of a mark when a count is missed', async () => {
    const user = userEvent.setup();
    const { fetchMock, posts } = playFetch([
      [': open\n\n', frame('beat', BEAT), frame('done', { status: 'awaiting_choice' })],
      [': open\n\n'],
    ]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    // The beat wants three. The child taps five.
    await user.click(await screen.findByTestId('count-5'));

    // Nothing was submitted, nothing was disabled, and nothing said "wrong".
    expect(posts).toHaveLength(0);
    expect(await screen.findByTestId('beat-scaffold')).toHaveTextContent(
      "Let's count them together",
    );
    expect(document.body.textContent).not.toMatch(/wrong|incorrect|oops|try again|no,/i);
    expect(screen.getByTestId('count-5')).not.toBeDisabled();

    // The next tap is accepted whatever it is — a child who has already had one go
    // is not made to keep guessing.
    await user.click(screen.getByTestId('count-4'));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({
      choiceKey: 'count',
      value: 4,
      retries: 1,
      assisted: true,
    });
  });

  it('records an easy answer as unassisted', async () => {
    const user = userEvent.setup();
    const { fetchMock, posts } = playFetch([
      [': open\n\n', frame('beat', BEAT), frame('done', { status: 'awaiting_choice' })],
      [': open\n\n'],
    ]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await user.click(await screen.findByTestId('count-3'));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toMatchObject({ value: 3, retries: 0, assisted: false });
  });

  it('carries on when the choice request fails', async () => {
    // The child is not the person who can fix a network error, so they are never
    // told about one (§13). Reopening re-reads whatever the server thinks is true.
    const user = userEvent.setup();
    const { fetchMock } = playFetch([DECISION_OPEN, DECISION_OPEN]);
    const failing = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes('/choice')) return Promise.reject(new TypeError('offline'));
      return fetchMock(url, init);
    });
    vi.stubGlobal('fetch', failing);

    renderStory();
    await user.click(await screen.findByTestId('choice-follow_river'));

    await waitFor(() => expect(screen.getByTestId('beat-narration')).toBeInTheDocument());
    expect(document.body.textContent).not.toMatch(/error|sorry|failed|offline/i);
  });
});

/**
 * The ending (§8.8, §14.3, §14.5).
 */
describe('the warm close', () => {
  const closeOpen = [': open\n\n', frame('beat', CLOSE), frame('done', { status: 'complete' })];

  it('offers two big voiced ways out, and no score', async () => {
    const { fetchMock } = playFetch([closeOpen]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();

    expect(await screen.findByTestId('warm-close')).toBeInTheDocument();
    expect(screen.getByTestId('close-again')).toHaveAccessibleName(/somewhere new with Pip/i);
    expect(screen.getByTestId('close-done')).toHaveAccessibleName(/all done/i);
    // Nothing to answer, because the close asks nothing.
    expect(screen.queryByTestId('choice-cards')).toBeNull();
    expect(screen.queryByTestId('count-pad')).toBeNull();
    expect(screen.queryByTestId('beat-continue')).toBeNull();
    // No score, no timer, no streak, no red X (§14.5).
    expect(document.body.textContent).not.toMatch(
      /score|points|stars earned|streak|minutes|well done!/i,
    );
    // And a reason to come back tomorrow (§8.8).
    expect(screen.getByTestId('beat-narration')).toHaveTextContent(/tomorrow/i);
  });

  it('ends the adventure when the child is finished', async () => {
    const user = userEvent.setup();
    const { fetchMock, posts } = playFetch([closeOpen]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await user.click(await screen.findByTestId('close-done'));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain('/adventures/adv-1/end');
    expect(posts[0].body).toMatchObject({ reason: 'completed' });
  });

  it('closes the old story before starting a new one', async () => {
    const user = userEvent.setup();
    const { fetchMock, posts } = playFetch([closeOpen, [': open\n\n', frame('beat', BEAT)]]);
    vi.stubGlobal('fetch', fetchMock);

    renderStory();
    await user.click(await screen.findByTestId('close-again'));

    // Ended first — an adventure left `in_progress` is one the next ask would
    // rejoin, and the child asked for somewhere new.
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain('/end');
    await waitFor(() =>
      expect(screen.getByTestId('beat-narration')).toHaveTextContent('Three little frogs'),
    );
  });
});
