/**
 * The seam between beats (§4.9, §14.6, §14.7, §15.8).
 *
 * Two rules are being defended here and they pull against each other, which is
 * why both need tests. **Nothing may ever indicate loading** — so the gap is
 * covered with motion and a line of story. And **motion must fully disable under
 * `prefers-reduced-motion`** — so all of that has to come apart cleanly, without
 * taking the story with it.
 *
 * The reduced-motion cases are the ones worth having. The failure they catch is
 * not "an animation still played": it is the previous beat's picture left
 * permanently on top of the current one, because the element that removes it was
 * waiting for an `animationend` that a person who asked for no animation will
 * never receive.
 */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SceneTransition from '@/components/SceneTransition';
import { REDUCED_MOTION_QUERY, waitingLine } from '@/lib/motion';

/** Point `matchMedia` at a fixed answer for the reduced-motion query. */
function setReducedMotion(reduced: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: query === REDUCED_MOTION_QUERY ? reduced : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    })),
  );
}

function Scene({ label }: { label: string }) {
  return <p data-testid={`scene-${label}`}>{label}</p>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('SceneTransition', () => {
  beforeEach(() => setReducedMotion(false));

  it('shows the scene it is given, with no motion when nothing is happening', () => {
    render(
      <SceneTransition sceneKey="beat-1">
        <Scene label="one" />
      </SceneTransition>,
    );

    expect(screen.getByTestId('scene-one')).toBeInTheDocument();
    // Calm is the default: no page-turn to play, no gap to cover.
    expect(screen.queryByTestId('scene-leaving')).toBeNull();
    expect(screen.queryByTestId('scene-pending')).toBeNull();
  });

  it('holds the outgoing scene while the new one turns in, so there is no blank frame', () => {
    const { rerender } = render(
      <SceneTransition sceneKey="beat-1">
        <Scene label="one" />
      </SceneTransition>,
    );
    rerender(
      <SceneTransition sceneKey="beat-2">
        <Scene label="two" />
      </SceneTransition>,
    );

    // Both on screen at once, which is the whole point — the picture area is
    // never empty between two perfectly good scenes.
    const leaving = screen.getByTestId('scene-leaving');
    expect(within(leaving).getByTestId('scene-one')).toBeInTheDocument();
    expect(
      within(screen.getByTestId('scene-current')).getByTestId('scene-two'),
    ).toBeInTheDocument();

    // The one on its way out is not announced twice to a screen reader (§14.6).
    expect(leaving).toHaveAttribute('aria-hidden', 'true');
  });

  it('drops the outgoing scene once its animation has finished', () => {
    const { rerender } = render(
      <SceneTransition sceneKey="beat-1">
        <Scene label="one" />
      </SceneTransition>,
    );
    rerender(
      <SceneTransition sceneKey="beat-2">
        <Scene label="two" />
      </SceneTransition>,
    );

    fireEvent.animationEnd(screen.getByTestId('scene-leaving'));

    expect(screen.queryByTestId('scene-leaving')).toBeNull();
    expect(screen.getByTestId('scene-two')).toBeInTheDocument();
  });

  it('drops it on a timer too, for a tab that was backgrounded mid-turn', () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <SceneTransition sceneKey="beat-1">
        <Scene label="one" />
      </SceneTransition>,
    );
    rerender(
      <SceneTransition sceneKey="beat-2">
        <Scene label="two" />
      </SceneTransition>,
    );
    expect(screen.getByTestId('scene-leaving')).toBeInTheDocument();

    // An element that is display:none when its animation would have started never
    // fires `animationend`, and the cost of trusting it alone is the previous
    // beat's picture stuck over the current one. `act` because the timer's whole
    // job is a state update.
    act(() => vi.advanceTimersByTime(1000));

    expect(screen.queryByTestId('scene-leaving')).toBeNull();
  });

  it('does not restart the turn when a media frame lands inside the same beat', () => {
    const { rerender } = render(
      <SceneTransition sceneKey="beat-1">
        <Scene label="one" />
      </SceneTransition>,
    );
    rerender(
      <SceneTransition sceneKey="beat-2">
        <Scene label="two" />
      </SceneTransition>,
    );
    fireEvent.animationEnd(screen.getByTestId('scene-leaving'));

    // The illustration arrives — same beat, new children. The page must not turn
    // again, or every asset landing would replay the transition.
    rerender(
      <SceneTransition sceneKey="beat-2">
        <Scene label="two-with-picture" />
      </SceneTransition>,
    );

    expect(screen.queryByTestId('scene-leaving')).toBeNull();
    expect(screen.getByTestId('scene-two-with-picture')).toBeInTheDocument();
  });

  it('covers a wait with motion and a line of story, and never a spinner', () => {
    render(
      <SceneTransition sceneKey="beat-2" pending caption="Pip leads you down the mossy path…">
        <Scene label="two" />
      </SceneTransition>,
    );

    expect(screen.getByTestId('scene-transition')).toHaveAttribute('data-pending');
    expect(screen.getByTestId('scene-pending')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByTestId('scene-caption')).toHaveTextContent('mossy path');
    // The scene the child was looking at is still there underneath it.
    expect(screen.getByTestId('scene-two')).toBeInTheDocument();
    // Nothing here says a system is busy (§13, §14.5).
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(document.body.textContent).not.toMatch(/loading|please wait|error/i);
  });

  describe('with prefers-reduced-motion', () => {
    beforeEach(() => setReducedMotion(true));

    it('swaps scenes instantly and never strands the old one on screen', () => {
      const { rerender } = render(
        <SceneTransition sceneKey="beat-1">
          <Scene label="one" />
        </SceneTransition>,
      );
      rerender(
        <SceneTransition sceneKey="beat-2">
          <Scene label="two" />
        </SceneTransition>,
      );

      // The regression this whole hook exists for: with no animation there is no
      // `animationend`, so an outgoing layer would never be removed. It is never
      // mounted instead.
      expect(screen.queryByTestId('scene-leaving')).toBeNull();
      expect(screen.queryByTestId('scene-one')).toBeNull();
      expect(screen.getByTestId('scene-two')).toBeInTheDocument();
    });

    it('still covers the wait, and still says something', () => {
      render(
        <SceneTransition sceneKey="beat-2" pending caption="Pip leads you down the mossy path…">
          <Scene label="two" />
        </SceneTransition>,
      );

      // The flow is unchanged — the child is still told what is happening, in
      // words. Only the moving light is gone, and that is a token-level change
      // (`motion-safe:`), not a different screen.
      expect(screen.getByTestId('scene-caption')).toHaveTextContent('mossy path');
      expect(screen.getByTestId('scene-two')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).toBeNull();
    });
  });
});

describe('waitingLine', () => {
  it('is a line of story about the companion, not a status', () => {
    const line = waitingLine('Pip', 3);

    expect(line).toContain('Pip');
    expect(line).not.toMatch(/loading|generating|please wait/i);
  });

  it('is stable for one beat, so it does not flicker while the child waits', () => {
    // This component re-renders on every arriving media frame. A random line
    // would change on each of them, and the child would watch the sentence
    // rewrite itself four times.
    expect(waitingLine('Pip', 2)).toBe(waitingLine('Pip', 2));
    expect(waitingLine('Pip', 0)).not.toBe(waitingLine('Pip', 1));
  });
});
