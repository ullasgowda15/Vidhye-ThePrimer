# Fixtures

**Audio files here are deliberately not committed.** See `.gitignore`.

Voice recordings are personal data. That matters little for an adult reading a
test sentence, but this project will eventually involve recordings of children,
and those must never enter a git repository — history is permanent and awkward
to scrub after the fact. Keeping every fixture local, without exception, means
there's no judgement call to get wrong later.

## Recreating the recordings

The two passages and their deliberately planted errors are documented in
[passage1.md](passage1.md) and [passage2.md](passage2.md). Both were read by an
adult imitating a child. Record them, save as `voice1.mp4` and `voice2.mp4` in
this directory, and the spike scripts will pick them up:

```bash
.venv/Scripts/python.exe research/asr_spikes/whisper_alignment.py research/asr_spikes/fixtures/voice1.mp4
.venv/Scripts/python.exe research/asr_spikes/phoneme_scoring.py research/asr_spikes/fixtures/voice2.mp4 research/asr_spikes/fixtures/passage2_expected.txt
```

Results won't match the recorded findings exactly — different voice, different
acoustics. The findings in [../README.md](../README.md) describe the *patterns*
that held, which is what should be reproducible.

## If real child recordings are ever used

Consent from a parent or guardian, stored outside the repo, deleted when no
longer needed. A licensed research corpus (MyST, CMU Kids) is the safer route
for accuracy work.
