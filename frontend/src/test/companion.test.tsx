/**
 * The companion picker and the imprint scene (PRODUCT.md §9.1, §4.3, §13).
 *
 * The two properties worth pinning here are product rules, not rendering
 * details: the picker sends catalogue *keys* rather than anything a child typed
 * into a prompt, and the child surface never shows a spinner or an error — a
 * companion that is not ready yet is a friend who is still asleep.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MeetCompanion from '@/routes/child/MeetCompanion';
import CompanionPicker from '@/routes/parent/CompanionPicker';
import { useAuthStore } from '@/store/auth';

const CATALOG = {
  archetypes: [
    {
      key: 'dragon',
      label: 'a small dragon',
      blurb: 'A little dragon who is braver than they look.',
      default_palette: 'ember',
      default_personality_preset: 'brave_spark',
    },
    {
      key: 'owl',
      label: 'a sleepy owl',
      blurb: 'A sleepy owl who knows the way in the dark.',
      default_palette: 'moss',
      default_personality_preset: 'calm_anchor',
    },
  ],
  palettes: [
    {
      key: 'ember',
      label: 'Warm ember',
      primary: '#e2803c',
      secondary: '#f4c67a',
      accent: '#8c4a2f',
      description: 'warm amber and rust',
    },
    {
      key: 'tide',
      label: 'Sea tide',
      primary: '#3f8fa8',
      secondary: '#a8dbe4',
      accent: '#1f5a6e',
      description: 'cool teal and sea glass',
    },
  ],
  personality_presets: [
    { key: 'brave_spark', label: 'Playful and brave', traits: ['playful'] },
    { key: 'calm_anchor', label: 'Calm and cosy', traits: ['calm'] },
  ],
};

const READY_COMPANION = {
  id: 'c1',
  child: 'child-1',
  name: 'Pip',
  archetype: 'dragon',
  palette: 'ember',
  assets_ready: true,
  assets_status: 'ready',
  portrait_url: '/media/assets/child-1/portrait.svg',
  expressions: { happy: '/media/assets/child-1/happy.svg' },
  greeting: {
    text: "Hi Aarav! I'm Pip. I've been waiting to meet you.",
    audio_url: '/media/assets/child-1/hello.wav',
  },
};

const WAKING_COMPANION = {
  ...READY_COMPANION,
  assets_ready: false,
  assets_status: 'pending',
  portrait_url: null,
  expressions: {},
  greeting: { text: READY_COMPANION.greeting.text, audio_url: null },
};

/** A fetch stub that answers by URL and records what was sent. */
function stubApi(routes: Record<string, unknown>, sent: { url: string; body: unknown }[] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string, init: RequestInit = {}) => {
      sent.push({ url, body: init.body ? JSON.parse(init.body as string) : null });
      const match = Object.keys(routes).find((key) => url.includes(key));
      if (!match) {
        return Promise.resolve({ ok: false, status: 404, text: async () => '{}' });
      }
      return Promise.resolve({
        ok: true,
        status: init.method === 'POST' ? 201 : 200,
        text: async () => JSON.stringify(routes[match]),
      });
    }),
  );
  return sent;
}

function renderWith(ui: React.ReactElement, path = '/') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[path]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={ui} />
          <Route path="/meet/:childId" element={ui} />
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

describe('companion picker', () => {
  it('offers the archetypes the API serves, not a list of its own', async () => {
    stubApi({ 'companion-catalog': CATALOG });

    renderWith(<CompanionPicker childId="child-1" childName="Aarav" onDone={() => {}} />);

    expect(await screen.findByRole('button', { name: /small dragon/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sleepy owl/ })).toBeInTheDocument();
  });

  it('sends catalogue keys, never labels or colours', async () => {
    const user = userEvent.setup();
    const sent = stubApi({ 'companion-catalog': CATALOG, companion: READY_COMPANION });

    renderWith(<CompanionPicker childId="child-1" childName="Aarav" onDone={() => {}} />);

    await user.click(await screen.findByRole('button', { name: /sleepy owl/ }));
    await user.type(screen.getByLabelText('What is their name?'), 'Nim');
    await user.click(screen.getByRole('button', { name: 'Sea tide' }));
    await user.click(screen.getByRole('button', { name: /Introduce them to Aarav/ }));

    await waitFor(() => {
      const post = sent.find((call) => call.url.endsWith('/companion'));
      expect(post?.body).toEqual({
        archetype: 'owl',
        name: 'Nim',
        palette: 'tide',
        personality_preset: 'calm_anchor',
      });
    });
  });

  it('is complete with an archetype and a name alone', async () => {
    const user = userEvent.setup();
    const sent = stubApi({ 'companion-catalog': CATALOG, companion: READY_COMPANION });

    renderWith(<CompanionPicker childId="child-1" childName="Aarav" onDone={() => {}} />);

    // Nothing touched but the name: §9.1's sixty-second setup.
    await user.type(await screen.findByLabelText('What is their name?'), 'Pip');
    await user.click(screen.getByRole('button', { name: /Introduce them to Aarav/ }));

    await waitFor(() => {
      const post = sent.find((call) => call.url.endsWith('/companion'));
      expect(post?.body).toEqual({
        archetype: 'dragon',
        name: 'Pip',
        palette: 'ember',
        personality_preset: 'brave_spark',
      });
    });
  });

  it('cannot be submitted without a name', async () => {
    stubApi({ 'companion-catalog': CATALOG });

    renderWith(<CompanionPicker childId="child-1" childName="Aarav" onDone={() => {}} />);

    expect(await screen.findByRole('button', { name: /Introduce them to Aarav/ })).toBeDisabled();
  });
});

describe('meeting the companion', () => {
  it('shows the portrait, the name and the spoken greeting', async () => {
    stubApi({ companion: READY_COMPANION });

    renderWith(<MeetCompanion />, '/meet/child-1');

    const portrait = await screen.findByTestId('companion-portrait');
    expect(portrait).toHaveAttribute('src', READY_COMPANION.portrait_url);
    expect(screen.getByRole('heading', { name: 'Pip' })).toBeInTheDocument();
    // The child's own name, out loud and in the caption (§4.3).
    expect(screen.getByText(/Hi Aarav!/)).toBeInTheDocument();
    expect(screen.getByTestId('companion-greeting-audio')).toHaveAttribute(
      'src',
      READY_COMPANION.greeting.audio_url,
    );
  });

  it('shows a sleeping friend rather than a spinner while the art is generated', async () => {
    stubApi({ companion: WAKING_COMPANION });

    renderWith(<MeetCompanion />, '/meet/child-1');

    expect(await screen.findByText(/waking up/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/still sleeping/i)).toBeInTheDocument();
    expect(screen.queryByTestId('companion-portrait')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('never shows the child an error when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 500, text: async () => '{}' }),
    );

    renderWith(<MeetCompanion />, '/meet/child-1');

    expect(await screen.findByText(/waking up/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText(/error/i)).not.toBeInTheDocument();
  });

  it('offers a tap when the browser refuses to autoplay', async () => {
    const user = userEvent.setup();
    const play = vi.fn().mockRejectedValueOnce(new Error('NotAllowedError'));
    vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockImplementation(play);
    stubApi({ companion: READY_COMPANION });

    renderWith(<MeetCompanion />, '/meet/child-1');

    // Autoplay is attempted first — the tap target is the fallback, not the plan.
    await waitFor(() => expect(play).toHaveBeenCalled());

    play.mockResolvedValue(undefined);
    await user.click(await screen.findByRole('button', { name: /Say hello to Pip/ }));

    await waitFor(() => expect(play).toHaveBeenCalledTimes(2));
  });
});
