/**
 * The parent dashboard (PRODUCT.md §12) — Day 12, criteria 3, 4 and 6.
 *
 * These are about what the *rendered page* says, which is where two of the day's
 * criteria actually live:
 *
 * * **Criterion 3** — insights carry no grades or percentiles. The API hands the
 *   client a `mastery` float, so "we do not show a grade" is a claim about this
 *   layer and only this layer, and the test scans the rendered text for one.
 * * **Criterion 4** — the transparency log is verbatim. The backend returns the
 *   stored beat; a client that truncated it to a preview would break the promise
 *   with the server none the wiser, so the assertion is an exact string match.
 * * **Criterion 6** — the IA renders under the Calm theme.
 *
 * There is also a test for the thing this surface must do that the child surface
 * must never do: show the parent an error.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Home from '@/routes/parent/Home';
import Insights from '@/routes/parent/Insights';
import SessionReport from '@/routes/parent/SessionReport';
import Transparency from '@/routes/parent/Transparency';
import WeeklyReport from '@/routes/parent/WeeklyReport';
import { useAuthStore } from '@/store/auth';

const CHILD = 'c1';
const SESSION = 's1';
const ADVENTURE = 'a1';

const ME = {
  id: 'p1',
  email: 'priya@example.com',
  display_name: 'Priya',
  locale: 'en-US',
  created_at: '2026-01-01T00:00:00Z',
  children: [
    {
      id: CHILD,
      first_name: 'Aarav',
      age: 5,
      birthdate: '2021-05-04',
      locale: 'en-US',
      interests: ['frogs'],
      fears: [],
      avoid_topics: [],
      intensity_ceiling: 1,
      session_cap_min: 20,
      pre_reader: true,
      age_out_of_range: false,
    },
  ],
};

const NARRATION =
  'The river slowed, and three moss frogs sat on three warm stones.\nPip counted them out loud, one for each.';

const REPORT = {
  session_id: SESSION,
  occurred_at: '2026-08-11T17:30:00Z',
  duration_s: 480,
  ended_reason: 'completed',
  beats: 5,
  adventure_id: ADVENTURE,
  adventure_title: 'The Frog Chorus',
  world_name: 'Whispering Woods',
  place_name: 'the Hollow Oak',
  key_choice: 'Chose "Follow the river". The river led them to the moss frogs.',
  objective_code: 'count_to_3',
  milestone_name: 'Counting and quantity',
  how_it_went: 'Counting 3 things — straight away, on their own.',
  highlight: 'Aarav named the little frog Sparkle.',
  highlight_source: 'model',
  skills: [
    {
      code: 'count_to_3',
      domain: 'numeracy',
      milestone: 'Counting and quantity',
      asks: 2,
      unaided: 2,
      assisted: 0,
      retried: 0,
      status: 'practising',
    },
  ],
};

const INSIGHTS = {
  milestones: [
    {
      name: 'Counting and quantity',
      domain: 'numeracy',
      description: 'Says how many things there are by counting them.',
      status: 'practising',
      furthest: 'can count a small group of visible things, up to three',
      skills: [
        {
          domain: 'numeracy',
          objective: 'count_to_3',
          milestone: 'Counting and quantity',
          description: 'Count a small group of visible things, up to three.',
          status: 'mastered',
          summary: 'Does this reliably now: can count a small group of visible things.',
          evidence_count: 6,
          // The number the page must never render, in the three forms a reader
          // would mistake for a score: 0.64, 0.6, and 64%.
          mastery: 0.64,
          reach: 0.64,
        },
      ],
    },
    {
      name: 'Words for the world',
      domain: 'vocabulary',
      description: 'Uses words for colours, sizes and shapes.',
      status: 'not_started',
      furthest: '',
      skills: [
        {
          domain: 'vocabulary',
          objective: 'name_colours',
          milestone: 'Words for the world',
          description: 'Point at whatever a named colour describes.',
          status: 'not_started',
          summary: 'Not covered yet — this one is about learning to point at colours.',
          evidence_count: 0,
          mastery: 0.0,
          reach: 1.0,
        },
      ],
    },
  ],
  interests: {
    now: ['frog', 'river'],
    earlier: ['dinosaur'],
    story: 'Over the last few weeks: dinosaur, and now frog.',
  },
};

const WEEKLY = {
  week: '2026-08-10/2026-08-16',
  sessions: 4,
  completionRate: 0.75,
  highlight: 'Aarav named the little frog Sparkle.',
  skills: [
    {
      domain: 'numeracy',
      objective: 'count_to_3',
      milestone: 'Counting and quantity',
      asks: 6,
      movement: 'does this reliably now',
      status: 'mastered',
    },
  ],
  milestone: {
    name: 'Counting and quantity',
    step: 'count a small group of visible things, up to three',
    reached: true,
  },
  interests_trend: ['frog', 'river'],
  recommendation: 'Aarav is deep in frog at the moment. Want the next few stories to stay there?',
};

const TRANSCRIPT = {
  adventureId: ADVENTURE,
  title: 'The Frog Chorus',
  world: 'Whispering Woods',
  place: 'the Hollow Oak',
  status: 'completed',
  startedAt: '2026-08-11T17:30:00Z',
  completedAt: '2026-08-11T17:38:00Z',
  beats: [
    {
      seq: 1,
      narration: NARRATION,
      emotion: 'playful_curious',
      imageUrl: null,
      safetyStatus: 'passed',
      asked: 'How many frogs can you see?',
      options: ['Three', 'Four'],
      answered: 'Answered 3',
    },
    {
      seq: 2,
      narration: 'The stones were warm and the frogs sang.',
      emotion: 'gentle_wonder',
      imageUrl: null,
      safetyStatus: 'rewritten',
      asked: '',
      options: [],
      answered: 'Tapped to carry on',
    },
  ],
};

/** Route → body. Anything unrouted 404s, so a page cannot pass on the wrong data. */
const ROUTES: [RegExp, unknown][] = [
  [/\/api\/v1\/me$/, ME],
  [/\/reports\/sessions$/, [{ ...REPORT, highlight_source: 'model' }]],
  [/\/reports\/session\//, REPORT],
  [/\/reports\/weekly/, WEEKLY],
  [/\/insights$/, INSIGHTS],
  [/\/memories$/, []],
  [
    /\/transcripts$/,
    [
      {
        adventureId: ADVENTURE,
        title: 'The Frog Chorus',
        status: 'completed',
        startedAt: '2026-08-11T17:30:00Z',
        beats: 2,
      },
    ],
  ],
  [/\/transcript$/, TRANSCRIPT],
];

function stubApi(overrides: [RegExp, unknown][] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string) => {
      const table = [...overrides, ...ROUTES];
      const match = table.find(([pattern]) => pattern.test(url));
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

beforeEach(() => {
  useAuthStore.setState({ accessToken: 'access-token', status: 'authenticated' });
  stubApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Criterion 6: the IA and the Calm theme
// ---------------------------------------------------------------------------
describe('the dashboard shell', () => {
  it('renders under the Calm theme with the sections §12.10 names', async () => {
    const { container } = renderAt(
      `/parent/child/${CHILD}/insights`,
      '/parent/child/:childId/insights',
      <Insights />,
    );

    expect(await screen.findByRole('heading', { name: /how aarav is growing/i })).toBeVisible();
    expect(container.querySelector('[data-theme="calm"]')).not.toBeNull();

    const nav = screen.getByRole('navigation', { name: /aarav's dashboard/i });
    for (const label of ['Reports', 'Weekly & growth', 'Insights', 'Transparency']) {
      expect(within(nav).getByRole('link', { name: label })).toBeVisible();
    }
  });

  it('leads Home with the child’s most recent real moment', async () => {
    renderAt('/parent', '/parent', <Home />);

    expect(await screen.findByText('Aarav named the little frog Sparkle.')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Criterion 3: plain language, no grades
// ---------------------------------------------------------------------------
describe('learning insights', () => {
  it('shows a status in words and never the number behind it', async () => {
    const { container } = renderAt(
      `/parent/child/${CHILD}/insights`,
      '/parent/child/:childId/insights',
      <Insights />,
    );

    expect(await screen.findByText('Counting and quantity')).toBeVisible();
    expect(screen.getAllByText('Does this reliably').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Not covered yet').length).toBeGreaterThan(0);

    // The mastery estimate is on the wire and must reach the screen in no form.
    const rendered = container.textContent ?? '';
    expect(rendered).not.toContain('%');
    expect(rendered).not.toContain('0.64');
    expect(rendered).not.toContain('64');
  });

  it('groups milestones by domain and names the domains for a person', async () => {
    renderAt(`/parent/child/${CHILD}/insights`, '/parent/child/:childId/insights', <Insights />);

    expect(await screen.findByRole('heading', { name: 'Numbers and counting' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Words and language' })).toBeVisible();
  });

  it('tells the parent where the interests came from', async () => {
    renderAt(`/parent/child/${CHILD}/insights`, '/parent/child/:childId/insights', <Insights />);

    expect(
      await screen.findByText('Over the last few weeks: dinosaur, and now frog.'),
    ).toBeVisible();
  });

  it('reports the week’s completion as a count rather than a percentage', async () => {
    const { container } = renderAt(
      `/parent/child/${CHILD}/weekly`,
      '/parent/child/:childId/weekly',
      <WeeklyReport />,
    );

    expect(await screen.findByText(/4 stories · 3 played to the end/)).toBeVisible();
    expect(container.textContent ?? '').not.toContain('%');
  });
});

// ---------------------------------------------------------------------------
// Criterion 5, client side: the report says where its sentence came from
// ---------------------------------------------------------------------------
describe('the session report', () => {
  it('says how the highlight was produced', async () => {
    renderAt(
      `/parent/child/${CHILD}/reports/${SESSION}`,
      '/parent/child/:childId/reports/:sessionId',
      <SessionReport />,
    );

    expect(await screen.findByText('Aarav named the little frog Sparkle.')).toBeVisible();
    expect(screen.getByText(/checked against what actually happened/i)).toBeVisible();
  });

  it('says plainly when nothing stood out, rather than dressing it up', async () => {
    stubApi([
      [
        /\/reports\/session\//,
        {
          ...REPORT,
          highlight: 'Aarav tapped through today’s story without adding much of their own.',
          highlight_source: 'quiet',
        },
      ],
    ]);

    renderAt(
      `/parent/child/${CHILD}/reports/${SESSION}`,
      '/parent/child/:childId/reports/:sessionId',
      <SessionReport />,
    );

    expect(await screen.findByText(/would rather say so than make something up/i)).toBeVisible();
  });

  it('never presents a session that ended early as one that did not', async () => {
    stubApi([[/\/reports\/session\//, { ...REPORT, ended_reason: 'exit' }]]);

    renderAt(
      `/parent/child/${CHILD}/reports/${SESSION}`,
      '/parent/child/:childId/reports/:sessionId',
      <SessionReport />,
    );

    expect(await screen.findByText(/Left partway through/)).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Criterion 4: the transparency log is verbatim
// ---------------------------------------------------------------------------
describe('the transparency log', () => {
  it('renders the stored narration word for word', async () => {
    renderAt(
      `/parent/child/${CHILD}/transparency/${ADVENTURE}`,
      '/parent/child/:childId/transparency/:adventureId',
      <Transparency />,
    );

    // Exact, not a preview: an ellipsis here would break §12.6 with the server
    // none the wiser.
    const told = await screen.findByText((_, node) => node?.textContent === NARRATION);
    expect(told).toBeVisible();
    expect(told.textContent).not.toContain('…');
  });

  it('shows what the child did under the beat that asked', async () => {
    renderAt(
      `/parent/child/${CHILD}/transparency/${ADVENTURE}`,
      '/parent/child/:childId/transparency/:adventureId',
      <Transparency />,
    );

    expect(await screen.findByText('Answered 3')).toBeVisible();
    expect(screen.getByText(/How many frogs can you see\?/)).toBeVisible();
  });

  it('admits when the safety layer rewrote a scene', async () => {
    renderAt(
      `/parent/child/${CHILD}/transparency/${ADVENTURE}`,
      '/parent/child/:childId/transparency/:adventureId',
      <Transparency />,
    );

    expect(await screen.findByText(/safety layer rewrote this scene/i)).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// The parent surface is allowed to show an error. The child surface is not.
// ---------------------------------------------------------------------------
describe('failure', () => {
  it('tells the parent plainly when a report could not be loaded', async () => {
    stubApi([[/\/reports\/session\//, undefined]]);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (/\/reports\/session\//.test(url)) {
          return Promise.resolve({ ok: false, status: 500, text: async () => '{}' });
        }
        const match = ROUTES.find(([pattern]) => pattern.test(url));
        return Promise.resolve({
          ok: true,
          status: 200,
          text: async () => JSON.stringify(match?.[1] ?? {}),
        });
      }),
    );

    renderAt(
      `/parent/child/${CHILD}/reports/${SESSION}`,
      '/parent/child/:childId/reports/:sessionId',
      <SessionReport />,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not load this/i);
  });
});
