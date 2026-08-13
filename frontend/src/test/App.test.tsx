import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { tokens } from '@/design/tokens';
import ChildHome from '@/routes/child/ChildHome';
import ParentHome from '@/routes/parent/Home';

function renderWithProviders(ui: React.ReactElement, initialPath = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('child route', () => {
  it('renders the storybook landing', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ status: 'ok', service: 'primer', version: '0.1.0' }),
      }),
    );

    renderWithProviders(<ChildHome />);

    expect(screen.getByRole('heading', { name: 'The Primer' })).toBeInTheDocument();
  });

  it('shows the health status once the API answers', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ status: 'ok', service: 'primer', version: '0.1.0' }),
      }),
    );

    renderWithProviders(<ChildHome />);

    expect(await screen.findByText('the story is awake')).toBeInTheDocument();
  });

  it('never surfaces a raw error to the child when the API is down', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')));

    renderWithProviders(<ChildHome />);

    // The story shell stays up; only the quiet dot changes.
    expect(screen.getByRole('heading', { name: 'The Primer' })).toBeInTheDocument();

    // §14.5, and the Day 1-15 audit's F-9: this used to assert the word
    // 'offline', three lines below a docstring promising the child no error
    // state. A failed health check is now indistinguishable, on this screen,
    // from a slow one — both say the same thing, in story words.
    expect(await screen.findByText('the story is dreaming')).toBeInTheDocument();
    for (const word of ['offline', 'error', 'failed', 'network', 'unavailable']) {
      expect(screen.queryByText(new RegExp(word, 'i'))).toBeNull();
    }
  });
});

describe('parent route', () => {
  it('renders under the Calm theme', () => {
    const { container } = renderWithProviders(<ParentHome />, '/parent');

    expect(screen.getByRole('heading', { name: 'Parent area' })).toBeInTheDocument();
    // The theme switch is a data attribute, not a component branch (§15.11).
    expect(container.querySelector('[data-theme="calm"]')).not.toBeNull();
  });
});

describe('design tokens', () => {
  it('exposes the semantic layer rather than raw values', () => {
    expect(tokens.color.primary).toBe('var(--primary)');
    expect(tokens.space[4]).toBe('var(--space-4)');
    expect(tokens.motion.base).toBe('var(--motion-base)');
  });
});
