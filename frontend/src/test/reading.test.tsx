/**
 * The reading moment (Docs/Build/Reading-Integration.md §8).
 *
 * Three product rules, none of them rendering details:
 *
 * 1. **Most children see nothing.** The server decides who is invited — age, the
 *    parent's `pre_reader` switch, whether words are on screen. The client never
 *    infers eligibility, and `offered: false` draws nothing at all rather than an
 *    empty panel or a disabled control.
 * 2. **Nothing is graded.** The response carries a warm line and no verdict, and
 *    a child who said nothing gets the same kindness as one who read it
 *    perfectly — because in Phase 1 nothing has listened, and implying otherwise
 *    would be a guess shown to a five-year-old as a fact.
 * 3. **Nothing can fail in front of the child.** A refused microphone, a browser
 *    with no recorder, a dead network: all of them end with the companion saying
 *    thank you (§13).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '@/lib/api/client';
import * as recorder from '@/lib/recorder';
import { ReadingMoment } from '@/routes/child/ReadingMoment';
import { useAuthStore } from '@/store/auth';

const CHILD = 'child-1';

function renderMoment() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReadingMoment childId={CHILD} companionName="Sparkle" />
    </QueryClientProvider>,
  );
}

/** A recorder that hands back a clip the moment `stop()` is called. */
function stubRecorder(audio: Blob | null = new Blob(['x'], { type: 'audio/webm' })) {
  let finish: (value: { audio: Blob | null; durationMs: number }) => void = () => undefined;
  const done = new Promise<{ audio: Blob | null; durationMs: number }>((resolve) => {
    finish = resolve;
  });
  const session = {
    done,
    stop: vi.fn(() => finish({ audio, durationMs: 2400 })),
    level: () => 0,
  };
  vi.spyOn(recorder, 'canRecord').mockReturnValue(true);
  vi.spyOn(recorder, 'record').mockResolvedValue(session);
  return session;
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: 'access-token', status: 'authenticated', user: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('who is invited', () => {
  it('draws nothing at all for a child who is not offered a line', async () => {
    vi.spyOn(api, 'readingInvitation').mockResolvedValue({ offered: false });

    const { container } = renderMoment();

    await waitFor(() => expect(api.readingInvitation).toHaveBeenCalled());
    expect(screen.queryByTestId('reading-moment')).not.toBeInTheDocument();
    // Not a hidden panel, not a disabled button — nothing. This is the ordinary
    // case: most children are pre-readers.
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the line and the invitation to a child who is', async () => {
    vi.spyOn(api, 'readingInvitation').mockResolvedValue({
      offered: true,
      passageId: 'p001',
      text: 'Sam has a big fish.',
      prompt: 'Will you read this one to me?',
    });

    renderMoment();

    expect(await screen.findByTestId('reading-line')).toHaveTextContent('Sam has a big fish.');
    expect(screen.getByText('Will you read this one to me?')).toBeInTheDocument();
  });

  it('draws nothing when the invitation could not be fetched', async () => {
    /* A screen with one fewer thing on it is a complete state. An error under a
       goodbye is not. */
    vi.spyOn(api, 'readingInvitation').mockRejectedValue(new Error('offline'));

    const { container } = renderMoment();

    await waitFor(() => expect(api.readingInvitation).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

describe('reading it', () => {
  beforeEach(() => {
    vi.spyOn(api, 'readingInvitation').mockResolvedValue({
      offered: true,
      passageId: 'p001',
      text: 'Sam has a big fish.',
      prompt: 'Will you read this one to me?',
    });
  });

  it('sends the recording and shows what the companion says back', async () => {
    const user = userEvent.setup();
    const session = stubRecorder();
    const submit = vi
      .spyOn(api, 'submitReading')
      .mockResolvedValue({ attemptId: 'a1', say: 'Thank you for reading to me.', kept: false });

    renderMoment();
    await user.click(await screen.findByTestId('reading-start'));
    await user.click(await screen.findByTestId('reading-done'));

    expect(session.stop).toHaveBeenCalled();
    await waitFor(() => expect(submit).toHaveBeenCalled());
    expect(submit.mock.calls[0][1].passageId).toBe('p001');
    expect(await screen.findByTestId('reading-reply')).toHaveTextContent(
      'Thank you for reading to me.',
    );
  });

  it('says nothing about how well it went', async () => {
    /* Rule 2. Nothing listened to the audio, so nothing may sound like it did —
       the wording is the server's and the client has no branch on quality. */
    const user = userEvent.setup();
    stubRecorder();
    vi.spyOn(api, 'submitReading').mockResolvedValue({
      attemptId: 'a1',
      say: 'I liked hearing your voice.',
      kept: false,
    });

    renderMoment();
    await user.click(await screen.findByTestId('reading-start'));
    await user.click(await screen.findByTestId('reading-done'));

    const reply = await screen.findByTestId('reading-reply');
    for (const word of ['wrong', 'correct', 'nearly', 'try again', 'well done']) {
      expect(reply.textContent?.toLowerCase()).not.toContain(word);
    }
  });

  it('still thanks the child when the upload fails', async () => {
    const user = userEvent.setup();
    stubRecorder();
    vi.spyOn(api, 'submitReading').mockRejectedValue(new Error('offline'));

    renderMoment();
    await user.click(await screen.findByTestId('reading-start'));
    await user.click(await screen.findByTestId('reading-done'));

    expect(await screen.findByTestId('reading-reply')).toHaveTextContent(/thank you/i);
  });

  it('treats a refused microphone as a turn where nobody spoke', async () => {
    /* Not the child's fault, and not something they should be told about. The
       server still gets a row: "invited, did not speak" is a real answer. */
    const user = userEvent.setup();
    vi.spyOn(recorder, 'canRecord').mockReturnValue(true);
    vi.spyOn(recorder, 'record').mockRejectedValue(new DOMException('no', 'NotAllowedError'));
    const submit = vi
      .spyOn(api, 'submitReading')
      .mockResolvedValue({ attemptId: 'a1', say: "That's alright. Another time.", kept: false });

    renderMoment();
    await user.click(await screen.findByTestId('reading-start'));

    await waitFor(() => expect(submit).toHaveBeenCalled());
    expect(submit.mock.calls[0][1].audio).toBeNull();
    expect(await screen.findByTestId('reading-reply')).toBeInTheDocument();
  });

  it('offers no control at all on a browser that cannot record', async () => {
    vi.spyOn(recorder, 'canRecord').mockReturnValue(false);

    renderMoment();

    expect(await screen.findByTestId('reading-start')).toBeDisabled();
  });
});
