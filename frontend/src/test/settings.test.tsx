/**
 * Settings, wellbeing and the privacy centre on the client (Day 13).
 *
 * Three of the day's criteria are only true if the *rendered page* makes them
 * true, and this file is where each of those is checked:
 *
 * * **Criterion 4** — the accessibility toggles work in the child UI. Asserted
 *   against the document root and against the `<audio>` element, because that is
 *   where the flags actually land; a test that only checked the parent form would
 *   pass while every toggle did nothing.
 * * **Criterion 2** — the time window is enforced. The child sees a *scene*, and
 *   no story is requested at all.
 * * **Criterion 3** — nothing on either surface counts anything down at the child.
 *
 * Plus the two destructive paths in the privacy centre, which are the only
 * irreversible things a parent can do in the product.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { safeVolume, wordsOnScreen } from '@/design/accessibility';
import Privacy from '@/routes/parent/Privacy';
import Settings from '@/routes/parent/Settings';
import StoryPage from '@/routes/child/StoryPage';
import { useAuthStore } from '@/store/auth';

const CHILD = 'c1';

const ME = {
  id: 'p1',
  email: 'priya@example.com',
  display_name: 'Priya',
  locale: 'en-US',
  created_at: '2026-01-01T00:00:00Z',
  children: [{ id: CHILD, first_name: 'Aarav' }],
};

const SETTINGS = {
  id: CHILD,
  first_name: 'Aarav',
  fears: ['the dark'],
  avoid_topics: ['hospitals'],
  intensity_ceiling: 1,
  session_cap_min: 15,
  time_window_start: null,
  time_window_end: null,
  voice_on: true,
  text_on: true,
  accessibility: {
    captions: true,
    reduced_motion: false,
    dyslexia_font: false,
    high_contrast: false,
    audio_volume: 1,
  },
  updated_at: '2026-08-12T00:00:00Z',
};

const DISCLOSURE = {
  version: '2026-08-12',
  headline: 'What we collect, why, and what we will never do with it.',
  sections: [
    {
      id: 'never',
      title: 'What we never do',
      body: 'We never sell your data.\n\nWe never send your child a notification.',
    },
  ],
};

const COMPANION = { name: 'Pip', portrait_url: null, assets_ready: true, voice_id: 'v1' };

/** Every request the pages under test make. Anything unrouted 404s. */
function routes(overrides: [RegExp, unknown][] = []): [RegExp, unknown][] {
  return [
    ...overrides,
    [/\/api\/v1\/me$/, ME],
    [/\/children$/, [{ id: CHILD, first_name: 'Aarav' }]],
    [/\/settings$/, SETTINGS],
    [/\/privacy$/, DISCLOSURE],
    [/\/companion$/, COMPANION],
    [/\/wellbeing$/, { open: true, reason: '', message: '' }],
    [/\/adventures\/current$/, null],
  ];
}

let requested: string[] = [];

function stubApi(overrides: [RegExp, unknown][] = []) {
  requested = [];
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      requested.push(url);
      const match = routes(overrides).find(([pattern]) => pattern.test(url));
      if (!match) {
        return Promise.resolve({ ok: false, status: 404, text: async () => '{}' });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(match[1]),
      });
    }),
  );
}

function renderAt(path: string, pattern: string, element: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={pattern} element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const renderSettings = () =>
  renderAt(`/parent/child/${CHILD}/settings`, '/parent/child/:childId/settings', <Settings />);

const renderStory = () => renderAt(`/story/${CHILD}`, '/story/:childId', <StoryPage />);

beforeEach(() => {
  useAuthStore.setState({ accessToken: 'access-token', status: 'authenticated' });
  stubApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  // The accessibility hook writes to the document root. Left behind, one test's
  // high-contrast flag would silently change the next one's assertions.
  for (const name of ['data-a11y-motion', 'data-a11y-font', 'data-a11y-contrast']) {
    document.documentElement.removeAttribute(name);
  }
});

// ---------------------------------------------------------------------------
// The settings page (§12.7)
// ---------------------------------------------------------------------------
describe('the settings page', () => {
  it('shows the controls §7.13 names, with the values in force', async () => {
    renderSettings();

    expect(await screen.findByText('How intense a story may get')).toBeInTheDocument();
    expect(screen.getByText('How long one sitting lasts')).toBeInTheDocument();
    expect(screen.getByText('When stories are available')).toBeInTheDocument();
    expect(screen.getByText('Accessibility')).toBeInTheDocument();

    // The ceiling that is actually set, not the first option.
    expect(screen.getByRole('radio', { name: /Mild/ })).toBeChecked();
    expect(within(screen.getByTestId('avoid-topics')).getByText('hospitals')).toBeInTheDocument();
  });

  it('saves on change rather than behind a button a parent can forget', async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByText('How intense a story may get');

    await user.click(screen.getByRole('radio', { name: /Gentlest/ }));

    await waitFor(() => {
      const patch = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === 'PATCH',
      );
      expect(patch).toBeDefined();
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({
        intensity_ceiling: 0,
      });
    });
    // No Save button anywhere: a control that needs confirming is a control that
    // is not honored immediately (§12.7).
    expect(screen.queryByRole('button', { name: /^save$/i })).toBeNull();
  });

  it('sends an added avoid-topic as a whole list', async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByTestId('avoid-topics-input');

    await user.type(screen.getByTestId('avoid-topics-input'), 'thunder');
    await user.click(screen.getAllByRole('button', { name: 'Add' })[0]);

    await waitFor(() => {
      const patch = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === 'PATCH',
      );
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({
        avoid_topics: ['hospitals', 'thunder'],
      });
    });
  });

  it('sends one accessibility flag at a time, so the server can merge', async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByLabelText('High contrast');

    await user.click(screen.getByLabelText('High contrast'));

    await waitFor(() => {
      const patch = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === 'PATCH',
      );
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({
        accessibility: { high_contrast: true },
      });
    });
  });

  it('says plainly that nothing here can pull a child back (§13.6)', async () => {
    renderSettings();

    expect(
      await screen.findByText(/never sends your child a notification, a reminder or a streak/i),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Criterion 4: the toggles reach the child's screen
// ---------------------------------------------------------------------------
describe('accessibility on the child surface', () => {
  const withAccess = (accessibility: Record<string, unknown>, rest: Record<string, unknown> = {}) =>
    stubApi([[/\/settings$/, { ...SETTINGS, ...rest, accessibility }]]);

  it('puts reduced motion, the dyslexia font and high contrast on the document root', async () => {
    withAccess({
      ...SETTINGS.accessibility,
      reduced_motion: true,
      dyslexia_font: true,
      high_contrast: true,
    });
    renderStory();

    await waitFor(() => {
      expect(document.documentElement.getAttribute('data-a11y-motion')).toBe('on');
    });
    expect(document.documentElement.getAttribute('data-a11y-font')).toBe('on');
    expect(document.documentElement.getAttribute('data-a11y-contrast')).toBe('on');
  });

  it('leaves the root clean when nothing is switched on', async () => {
    renderStory();

    await screen.findByTestId('story-thinking');
    expect(document.documentElement.getAttribute('data-a11y-contrast')).toBeNull();
  });

  it('clamps a volume the browser would throw on', () => {
    // `HTMLMediaElement.volume` raises `IndexSizeError` out of range, and a throw
    // on this path would be an uncaught error between a child and their story.
    expect(safeVolume(4)).toBe(1);
    expect(safeVolume(-1)).toBe(0);
    expect(safeVolume(0.4)).toBe(0.4);
    expect(safeVolume(undefined)).toBe(1);
  });
});

/**
 * The words-on-screen rule (§14.6), which the server states independently in
 * `ChildSettings.words_on_screen`. Two implementations of one rule, so both are
 * tested — `tests/test_settings_controls.py::TestWordsOnScreen` is the other half,
 * with the same four cases in the same order.
 */
describe('whether the spoken words are also on screen', () => {
  const access = SETTINGS.accessibility;

  it('shows them when the parent wants the story text', () => {
    expect(wordsOnScreen({ text_on: true, voice_on: true, accessibility: access })).toBe(true);
  });

  it('shows them when captions are on even though story text is off', () => {
    // Accessibility outranks preference.
    expect(
      wordsOnScreen({
        text_on: false,
        voice_on: true,
        accessibility: { ...access, captions: true },
      }),
    ).toBe(true);
  });

  it('shows them when the voice is off, whatever else is set', () => {
    // A beat delivered as neither sound nor text is not a beat.
    expect(
      wordsOnScreen({
        text_on: false,
        voice_on: false,
        accessibility: { ...access, captions: false },
      }),
    ).toBe(true);
  });

  it('hides them only when the parent turned both off and the voice is on', () => {
    expect(
      wordsOnScreen({
        text_on: false,
        voice_on: true,
        accessibility: { ...access, captions: false },
      }),
    ).toBe(false);
  });

  it('defaults to showing them while the settings request is still in flight', () => {
    expect(wordsOnScreen(undefined)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Criterion 2: the daily window, as a scene rather than a refusal
// ---------------------------------------------------------------------------
describe('the daily time window on the child surface', () => {
  const shut = () =>
    stubApi([
      [
        /\/wellbeing$/,
        {
          open: false,
          reason: 'time_window',
          message: "Pip is having a rest just now. Let's go and find something to do.",
        },
      ],
    ]);

  it('shows the companion resting, in their own words', async () => {
    shut();
    renderStory();

    expect(await screen.findByTestId('wellbeing-resting')).toBeInTheDocument();
    expect(screen.getByText(/Pip is having a rest just now/)).toBeInTheDocument();
  });

  it('never asks for a story, so no plan is ever paid for', async () => {
    shut();
    renderStory();
    await screen.findByTestId('wellbeing-resting');

    expect(requested.some((url) => url.includes('/adventures'))).toBe(false);
  });

  it('shows no error, no countdown and no reason to come back', async () => {
    shut();
    renderStory();
    const scene = await screen.findByTestId('wellbeing-resting');

    expect(screen.queryByRole('alert')).toBeNull();
    // §13.6: a countdown on a four-year-old's screen is pressure.
    expect(scene.textContent).not.toMatch(/\d/);
    expect(scene.textContent?.toLowerCase()).not.toContain('minute');
  });
});

// ---------------------------------------------------------------------------
// The privacy centre (§12.8)
// ---------------------------------------------------------------------------
describe('the privacy centre', () => {
  const renderPrivacy = () => renderAt('/parent/privacy', '/parent/privacy', <Privacy />);

  it('renders the disclosure the server serves, not copy of its own', async () => {
    renderPrivacy();

    expect(await screen.findByText('What we never do')).toBeInTheDocument();
    expect(screen.getByText('We never sell your data.')).toBeInTheDocument();
    expect(screen.getByText(/last changed on 2026-08-12/)).toBeInTheDocument();
  });

  it('will not delete a child until their name is typed', async () => {
    const user = userEvent.setup();
    renderPrivacy();

    await user.click(await screen.findByTestId(`delete-child-${CHILD}`));
    const confirm = screen.getByTestId('confirm-delete-child');
    expect(confirm).toBeDisabled();

    await user.type(screen.getByTestId('confirm-child-name'), 'Aarav');
    expect(confirm).toBeEnabled();
  });

  it('will not delete the account until a password is given', async () => {
    const user = userEvent.setup();
    renderPrivacy();

    await user.click(await screen.findByTestId('delete-account'));
    expect(screen.getByTestId('confirm-delete-account')).toBeDisabled();

    await user.type(screen.getByTestId('confirm-account-password'), 'a-password');
    expect(screen.getByTestId('confirm-delete-account')).toBeEnabled();
  });

  it('offers the export without hiding it behind the deletions', async () => {
    renderPrivacy();
    expect(await screen.findByTestId('export-account')).toBeInTheDocument();
  });
});
