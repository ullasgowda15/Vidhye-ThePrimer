# ASR Spike — Test 2 (longer passage, more error variety)

## 1. The correct passage (45 words) — what the app expects

> Meg has a pet cat. The cat is black and white. Meg likes to pet the cat each day. The cat likes to nap on the mat. When Meg claps her hands, the cat runs fast. Then the cat jumps on the bed and sleeps.

This is also saved in `passage2_expected.txt` — don't read this version aloud, it's the reference the script compares against.

## 2. What to actually read out loud

Read this version instead — mistakes are baked in, plus one clean sentence at the start with no errors, so we have a baseline of "normal" reading in the same clip.

> Meg has a pet cat. The cat is and white white. Meg likes to pat the cat each day. The cat likes to map on the mat. When Meg *(pause ~2 sec)* claps her hands, the cat runs fast. Den the cat ju— jumps on the bed and um sleeps.

Notes on the non-literal bits:
- *(pause ~2 sec)* — stop and pause silently, don't say the word "pause."
- `ju— jumps` — stumble out loud: say "ju—" and cut off, then say "jumps" correctly right after.
- Everything else in that block (including "Den" and "um") — just say the words as written, naturally.

## What each change is testing

| # | Sentence | Where | What you'll say | Error type |
|---|---|---|---|---|
| — | 1 | *(whole sentence)* | read exactly as-is | Clean control — no error, just a baseline |
| 1 | 2 | "black" | *(skip it)* | Omission |
| 2 | 2 | "white" | "white white" | Repetition |
| 3 | 3 | "pet" (2nd one) | "pat" | Substitution |
| 4 | 4 | "nap" | "map" | Substitution (visually-similar-letter confusion, b/m/n style) |
| 5 | 5 | before "claps" | *(silent pause ~2 sec)* | Hesitation |
| 6 | 6 | "Then" | "Den" | Digraph mispronunciation (dropped "th" sound) |
| 7 | 6 | "jumps" | "ju— jumps" | Self-correction |
| 8 | 6 | before "sleeps" | "um" | Filler-word insertion |

Sentence 6 stacks three errors close together on purpose — that's a deliberate stress test to see whether the alignment can still correctly separate adjacent errors (this tripped it up a bit in test 1, though the labeling fix mostly handled it).

## Recording

- Save the file into `experiments/asr_spike/` (any audio/video format is fine).
- Tell me the filename once it's there.

## Running it

```
.venv/Scripts/python.exe experiments/asr_spike/analyze.py experiments/asr_spike/<your_file> experiments/asr_spike/passage2_expected.txt
```
