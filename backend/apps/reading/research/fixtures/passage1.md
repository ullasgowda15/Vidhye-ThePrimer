# ASR Spike — Recording Instructions

## 1. The correct passage (18 words) — what the app expects

> Sam has a big fish. The fish can swim fast. Sam and the fish sit on the ship.

This is the "ground truth" the analysis script compares against — you don't read this one aloud, it's just for reference.

## 2. What to actually read out loud

Read this version instead — the mistakes are already baked into the words, so just read it naturally as written. Two notes:

- Where you see *(pause ~2 sec)*, stop and pause silently for a couple seconds — don't say the words "pause" out loud.
- Where you see `si— sit`, actually stumble on it out loud: say "si—" and cut yourself off, then say "sit" correctly right after — like a real self-correction.

> Sam has a dish. The fish can *(pause ~2 sec)* swim fast. Sam and the fish si— sit on the sip.

## What each change is testing

| # | Where | What you'll say | Error type |
|---|---|---|---|
| 1 | "big" | *(nothing — just skip it)* | Omission |
| 2 | "fish" (1st) | "dish" | Substitution |
| 3 | before "swim" | *(silent pause ~2 sec)* | Hesitation |
| 4 | "sit" | "si— sit" (stumble then correct) | Self-correction |
| 5 | "ship" | "sip" | Digraph mispronunciation (dropped "sh" sound) |

Everything else in the passage — Sam, has, a, The, fish (2nd and 3rd), can, fast, Sam, and, the, on, the — read normally and correctly.

## Recording

- Any format is fine (m4a, mp3, wav, whatever your phone/PC produces).
- Save the file into this folder: `experiments/asr_spike/`
- Tell me the filename once it's there.

## What we're checking

Whether the pipeline (Whisper transcription + alignment against the known passage) correctly flags all 5 planted errors at the right word, without flagging any of the correctly-read words as errors (false positives are just as bad as misses).
