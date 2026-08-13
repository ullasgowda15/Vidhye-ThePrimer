/**
 * Every error path in the child UI, swept (PRODUCT.md §13, §4.9, Day 15's §4.3).
 *
 * The criterion is absolute — *"**Every** error path in the child UI degrades to
 * a safe fallback (no raw errors ever reach a child)"* — so this file is
 * organised as the list of ways this screen can fail, each one walked to what a
 * four-year-old actually ends up looking at.
 *
 * `fallback.test.tsx` (Day 7) covers the *expected* failures: a refused
 * generation arriving as an ordinary beat, a dead stream, a failed choice POST.
 * Those still hold and are not repeated. This file covers the two that had no
 * handler until Day 15:
 *
 *   1. **a render-time exception** — the one path that could still white-screen a
 *      child, because React unmounts the tree and React Router's default error
 *      element writes a stack trace onto the page;
 *   2. **a beat with no voice** — not an error at all on the wire, and the reason
 *      it belongs here: for a parent who had turned the story text off, a beat
 *      whose narration was never synthesised is delivered as neither sound nor
 *      text, which is a blank screen assembled out of two working features.
 *
 * The standard applied to every case is the product's, not a testing one: is
 * there a character on screen, is there something to do, and does the word
 * "error" appear nowhere.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitForElementToBeRemoved } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChildBoundary, ParentBoundary } from '@/components/SafeBoundary';
import { wordsOnScreen } from '@/design/accessibility';
import StoryPage from '@/routes/child/StoryPage';
import { useAuthStore } from '@/store/auth';

/** A component that throws on render, the way a bad field on a beat would. */
function Explodes(): never {
  throw new Error('Cannot read properties of null (reading "illustration_prompt")');
}

describe('a render-time exception on the child surface', () => {
  beforeEach(() => {
    // React logs caught errors itself; the boundary logs its own. Neither is
    // interesting here and both are noisy.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the companion resting, and never the error', () => {
    render(
      <ChildBoundary>
        <Explodes />
      </ChildBoundary>,
    );

    expect(screen.getByTestId('child-safe-boundary')).toBeInTheDocument();

    // The exception's own text — the string React Router would have printed.
    expect(screen.queryByText(/Cannot read properties/)).toBeNull();

    // And none of the vocabulary a child must never meet on this surface.
    const page = screen.getByTestId('child-safe-boundary').textContent ?? '';
    for (const word of ['error', 'sorry', 'wrong', 'failed', 'unavailable', 'problem']) {
      expect(page.toLowerCase()).not.toContain(word);
    }
  });

  it('leaves the child a character and one big way onward', () => {
    render(
      <ChildBoundary>
        <Explodes />
      </ChildBoundary>,
    );

    // A face, labelled the way the wellbeing rest state is labelled (§13.6) —
    // the same character in the same posture, not a new "something broke" icon.
    expect(screen.getByLabelText(/having a rest/i)).toBeInTheDocument();

    const button = screen.getByTestId('child-safe-boundary-home');
    expect(button).toHaveAccessibleName('Go back to the beginning');
    // §14.4's forgiving target, on the one control a child has here.
    expect(button.className).toContain('min-h-target');
  });

  it('tells the grown-ups everything it does not tell the child', () => {
    const seen: Error[] = [];
    render(
      <ChildBoundary onError={(error) => seen.push(error)}>
        <Explodes />
      </ChildBoundary>,
    );

    // A degradation nobody can see is one that runs for a week.
    expect(seen).toHaveLength(1);
    expect(seen[0].message).toContain('illustration_prompt');
    expect(console.error).toHaveBeenCalled();
  });

  it('renders its children untouched when nothing throws', () => {
    render(
      <ChildBoundary>
        <p data-testid="ordinary">Pip peeks over the log.</p>
      </ChildBoundary>,
    );
    expect(screen.getByTestId('ordinary')).toBeInTheDocument();
    expect(screen.queryByTestId('child-safe-boundary')).toBeNull();
  });
});

describe('the same failure on the parent surface', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('tells the parent the truth, because a parent can act on it', () => {
    render(
      <ParentBoundary>
        <Explodes />
      </ParentBoundary>,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/did not load/i);
    // §12's transparency: they are told what is *not* affected, which is the part
    // a parent actually worries about.
    expect(alert).toHaveTextContent(/unchanged/i);
    // But not the stack trace. That goes to the console, where it is useful.
    expect(screen.queryByText(/Cannot read properties/)).toBeNull();
  });
});

describe('a beat that arrives without its voice', () => {
  /**
   * The rule under test is `wordsOnScreen`'s third clause, and the case is the
   * combination nobody chose: `voice_on` is true, `text_on` is false, and the
   * narration was never synthesised — a TTS vendor out of quota, or §18.8's
   * budget breaker declining a new line.
   */
  const textOff = { text_on: false, voice_on: true, accessibility: { captions: false } };

  it('shows the words rather than nothing at all', () => {
    expect(wordsOnScreen(textOff, false)).toBe(true);
  });

  it('keeps them off when the voice did arrive', () => {
    expect(wordsOnScreen(textOff, true)).toBe(false);
  });

  it('still honours captions and a switched-off voice', () => {
    expect(wordsOnScreen({ ...textOff, accessibility: { captions: true } }, true)).toBe(true);
    expect(wordsOnScreen({ ...textOff, voice_on: false }, true)).toBe(true);
  });

  it('defaults to assuming the voice is there, for callers with no opinion', () => {
    // The parent settings preview and the server-side mirror of this rule both
    // ask the question without a beat in hand; they must behave as before.
    expect(wordsOnScreen(textOff)).toBe(false);
  });
});

/**
 * The same rule, through the page that has to apply it (Day 15 review, P2-2).
 *
 * The four cases above grade `wordsOnScreen` in isolation, which leaves the
 * argument untested: `StoryPage` decides what to pass as `hasAudio`, and a
 * perfect rule asked the wrong question is the bug the rule exists to prevent.
 * The expression is `status === 'thinking' || audioUrl !== null`, and both halves
 * carry weight — the first keeps the words from flashing up during the second
 * before the `media` frame lands, the second is the actual answer once the beat
 * has settled.
 *
 * So these drive the real page, over a real stream, for a parent who turned the
 * story text off, and read what a child would end up looking at.
 */
describe('the story page applying that rule', () => {
  const SETTINGS = {
    text_on: false,
    voice_on: true,
    accessibility: { captions: false, reduced_motion: false, audio_volume: 1 },
  };

  const BEAT = {
    beatId: 'beat-1',
    seq: 1,
    narration: 'Pip peeks over the log. Three little frogs!',
    scene: { imageUrl: null, status: 'pending' },
    audioUrl: null,
    interaction: { type: 'none', prompt_audio: null, expected: null, choices: null },
    choices: null,
    emotion: 'playful_curious',
    isFinal: false,
  };

  const ADVENTURE = {
    adventureId: 'adv-1',
    streamUrl: '/api/v1/adventures/adv-1/stream',
    companion: { name: 'Pip', voiceId: 'warm_low' },
    estimatedBeats: 6,
    world: { key: 'whispering_woods', name: 'the Whispering Woods', place: 'the Hollow Oak' },
  };

  const asFrame = (event: string, data: unknown) =>
    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

  function play(chunks: string[]) {
    const encoder = new TextEncoder();
    let index = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (index >= chunks.length) return controller.close();
        controller.enqueue(encoder.encode(chunks[index++]));
      },
    });

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        const path = String(url);
        if (path.includes('/stream')) return Promise.resolve({ ok: true, status: 200, body });
        const payload = path.includes('/settings') ? SETTINGS : ADVENTURE;
        return Promise.resolve({
          ok: true,
          status: 200,
          text: async () => JSON.stringify(payload),
        });
      }),
    );

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/story/child-1']}>
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

  it('shows the narration when the voice never arrived', async () => {
    play([
      ': open\n\n',
      asFrame('beat', BEAT),
      // No `media` frame carrying audio — the vendor was out of quota, or the
      // breaker declined the line. The stream then settles.
      asFrame('done', { status: 'complete' }),
    ]);

    // Text is off and captions are off, so this paragraph is on screen for one
    // reason only: it is that or a child sitting in front of nothing.
    expect(await screen.findByTestId('beat-narration')).toHaveTextContent(/three little frogs/i);
  });

  it('keeps the words off once the voice does arrive', async () => {
    play([
      ': open\n\n',
      asFrame('beat', BEAT),
      asFrame('media', { audioUrl: 'https://cdn.example/beat-1.mp3' }),
      asFrame('done', { status: 'complete' }),
    ]);

    // The audio landed, so the parent's choice stands.
    await screen.findByTestId('beat-audio');
    expect(screen.queryByTestId('beat-narration')).toBeNull();
  });

  it('does not flash the words up while the audio is still on its way', async () => {
    // The gap the `status === 'thinking'` half of the expression exists to cover:
    // between the `beat` frame and the `media` frame there is genuinely no audio,
    // and treating that as "no voice is coming" would put a paragraph on screen
    // and snatch it away a moment later.
    play([': open\n\n', asFrame('beat', BEAT)]);

    // `story-thinking` renders only while there is no beat, so its removal is the
    // moment the frame landed — which is what stops this passing vacuously on a
    // page that simply never got one.
    await waitForElementToBeRemoved(() => screen.queryByTestId('story-thinking'));
    expect(screen.queryByTestId('beat-narration')).toBeNull();
  });
});
