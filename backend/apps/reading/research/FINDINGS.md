# ASR spikes — can we detect *specific* reading errors?

The product's whole claim rests on one mechanism: a child reads aloud, and the
app identifies the specific thing that went wrong — not just "that was wrong",
but *which phonics pattern broke*. These spikes tested whether that's achievable
before building anything around it.

The scripts here are throwaway. **The findings are the deliverable.**

## Setup

Every approach below runs locally and free — no cloud speech vendor, no account,
no API key. That was a deliberate constraint (and removes vendor risk from the
core mechanic).

## Fixtures

Two recordings, each read with a documented set of deliberately planted errors,
so results can be scored against known ground truth rather than eyeballed.

| Fixture | Passage | Planted errors |
|---|---|---|
| `voice1.mp4` | [passage1.md](fixtures/passage1.md) | 5 — omission, substitution, hesitation, digraph mispronunciation, self-correction |
| `voice2.mp4` | [passage2.md](fixtures/passage2.md) | 8 — adds repetition, filler word, a visually-confusable substitution, and a clean control sentence |

Read by an adult imitating a child. Real 5–7-year-old speech is still untested —
see "Open questions".

## Approach 1 — Whisper + known-text alignment

`whisper_alignment.py`. Since the target passage is always known, this is a
comparison problem, not open transcription: transcribe with word timestamps,
diff against the expected text, and interpret the gaps.

**Works well:** omissions, substitutions, self-corrections (the stutter shows up
as an extra word), and hesitations.

**Two fixes that mattered:**
- Hesitation isn't a *gap before* a word — Whisper folds the pause into the
  word's **duration** (a hesitant "swim" ran 1.84s against a ~0.4s baseline).
  Detect long duration relative to the clip median, not silence between words.
- `vad_filter=True` stops leading/trailing silence inflating the first and last
  word's timing.

**Where it breaks — and this is the important part:** Whisper's language model
quietly *corrects* mispronunciations back to the expected word. "Then" read
aloud as "Den" came back as "Then" at **0.92 confidence** — no signal at all.
This is the single error class the product most needs, and word-level
transcription structurally cannot see it.

**Low confidence is not a usable substitute.** Across both fixtures it missed a
real error at 0.92 while flagging correctly-read words as low as 0.19, and gave
different numbers on identical audio between runs. It's reported to the UI as
"unclear", never as a verdict.

**A bigger Whisper model would likely make this worse, not better** — more
language-model pull means more auto-correcting away of the very errors we want.

## Approach 2 — phoneme-level scoring

`phoneme_scoring.py`. Instead of "what word was that", ask "how well did the
actual sounds match the sounds this word should contain". Uses `wav2vec2` for
phoneme recognition and `espeak` (via `phonemizer`) for the expected sequence.

**The result that matters:** "ship" read as "sip" was caught precisely —
expected `ʃ ɪ p`, detected `s ɪ p`, pinpointing the `ʃ`→`s` swap. That is
exactly the digraph error word-confidence was blind to, and it's diagnosable
feedback: *"you dropped the sh sound"*.

**Weakness: vowels are noisy.** `a` (eɪ→ɐ), `fast` (æ→ɑː), `on` (ɔ→ʌ) all
flagged as mismatches when read fine. Two causes, only one of them a bug:
1. **Bug (fixable):** words are phonemized in isolation, giving dictionary-form
   vowels that don't match natural connected speech.
2. **Real:** accent variation. Consonants held up; vowels need tolerance rules,
   probably a per-speaker baseline.

**Conclusion:** phoneme-level is the right mechanism for mispronunciation.
Word-level alignment remains better for omissions and timing. They're
complementary, not competing.

## Practical notes

- **`av` and `torch` segfault in the same process** on this machine. Audio
  decoding runs in a subprocess (`decode_audio.py`) as a result.
- Phoneme scoring is slow on CPU (~300M params). Fine for a spike; needs a GPU
  or a smaller model in production.
- Whisper timestamps are numpy scalars, which don't serialize to JSON.
- Passing the expected passage as Whisper's `initial_prompt` was **tried and
  reverted** — it didn't help the case it targeted and made confidence noisy
  across unrelated correctly-read words.

## Open questions

- **Real child speech is untested.** Both fixtures are an adult imitating one.
  Acoustics differ enough that this needs a licensed corpus (MyST, CMU Kids) or
  consented recordings before any accuracy claim is credible.
- Vowel tolerance for accent variation — unsolved.
- Filler words ("um") vanish entirely from Whisper output. Deprioritised: that's
  a fluency signal, not a decoding error.

## Running them

```bash
.venv/Scripts/python.exe research/asr_spikes/whisper_alignment.py research/asr_spikes/fixtures/voice1.mp4
.venv/Scripts/python.exe research/asr_spikes/phoneme_scoring.py research/asr_spikes/fixtures/voice1.mp4
```

Both take an optional second argument: a file containing the expected text
(defaults to passage 1).
