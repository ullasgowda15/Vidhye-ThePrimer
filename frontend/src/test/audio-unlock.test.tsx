/**
 * The first sound (PRODUCT.md §4.9, §14.5, §13).
 *
 * Reported from the pilot as *"silent at the beginning, then it appears once
 * engaged"*, which is the signature of a browser autoplay refusal rather than
 * anything wrong with the voice pipeline. Every narration is started from an
 * effect when its audio URL arrives — programmatic playback, which browsers
 * decline until the document has been touched. Walking in through the child
 * picker there is always a tap first; landing straight on `/welcome/:childId`
 * from a reload, a bookmark or a PWA launch there is not.
 *
 * Two behaviours are pinned here, and the second only became possible once the
 * first existed:
 *
 * 1. A refusal is **noticed**. The old code mapped a resolved play and a
 *    rejected one both to `undefined`, so no screen could tell whether the
 *    companion was speaking or had been silenced.
 * 2. A refusal puts something **on the glass** — a tap cue with no words in it,
 *    because the audience cannot read. And when playback succeeds the cue must
 *    not appear at all, which is the half that stops it becoming decoration.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { playOrReportBlocked, primeAudioOnFirstGesture } from '@/lib/audioUnlock';
import MeetCompanion from '@/routes/child/MeetCompanion';
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

function stubCompanion() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Promise.resolve(
        new Response(JSON.stringify(COMPANION), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    ),
  );
}

function renderMeet() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/meet/child-1']}>
        <Routes>
          <Route path="/meet/:childId" element={<MeetCompanion />} />
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
  vi.restoreAllMocks();
});

describe('reporting whether the browser allowed playback', () => {
  it('says true when the sound started', async () => {
    const element = { play: () => Promise.resolve() } as unknown as HTMLAudioElement;

    await expect(playOrReportBlocked(element)).resolves.toBe(true);
  });

  it('says false when the browser refused, rather than throwing', async () => {
    /* A refusal is a normal state on a fresh document, not an exception. If this
       rejected, every call site would need a try/catch and the one that forgot
       would crash the child surface (§13). */
    const element = {
      play: () => Promise.reject(new DOMException('play() failed', 'NotAllowedError')),
    } as unknown as HTMLAudioElement;

    await expect(playOrReportBlocked(element)).resolves.toBe(false);
  });

  it('treats an engine that returns no promise as success', async () => {
    /* Older engines return undefined from play(). They predate the autoplay
       policies this module exists for, so silence from them means it worked. */
    const element = { play: () => undefined } as unknown as HTMLAudioElement;

    await expect(playOrReportBlocked(element)).resolves.toBe(true);
  });
});

describe('priming audio on the first gesture', () => {
  it('plays a moment of silence when the user first touches the page', async () => {
    const play = vi.fn(() => Promise.resolve());
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(play);

    const detach = primeAudioOnFirstGesture();
    window.dispatchEvent(new Event('pointerdown'));

    await waitFor(() => expect(play).toHaveBeenCalled());
    detach();
  });
});

describe('what a child sees when the companion is silenced', () => {
  it('shows a wordless tap cue when playback was refused', async () => {
    stubCompanion();
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() =>
      Promise.reject(new DOMException('play() failed', 'NotAllowedError')),
    );

    renderMeet();

    const cue = await screen.findByTestId('tap-to-hear');

    /* No sentence. The audience cannot read, so the invitation has to be a
       picture — the accessible name is for a parent's screen reader, and is the
       only place words are allowed. */
    expect(cue).toHaveTextContent('');
    expect(cue).toHaveAccessibleName(/tap anywhere to hear sparkle/i);
  });

  it('shows nothing at all when the companion simply speaks', async () => {
    stubCompanion();
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve());

    renderMeet();

    await screen.findByTestId('companion-portrait');
    await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalled());

    /* The half that keeps the cue honest. A prompt that appears on every visit
       is decoration, and it would teach a child to tap before listening. */
    expect(screen.queryByTestId('tap-to-hear')).not.toBeInTheDocument();
  });

  it('takes the cue away once the sound is under way', async () => {
    stubCompanion();
    const user = userEvent.setup();
    let allowed = false;
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() =>
      allowed ? Promise.resolve() : Promise.reject(new DOMException('no', 'NotAllowedError')),
    );

    renderMeet();
    const cue = await screen.findByTestId('tap-to-hear');

    // The tap that a real child makes on the refused screen. The container
    // carries the handler, which is why the cue itself is pointer-transparent.
    allowed = true;
    await user.click(cue);

    await waitFor(() => expect(screen.queryByTestId('tap-to-hear')).not.toBeInTheDocument());
  });
});
