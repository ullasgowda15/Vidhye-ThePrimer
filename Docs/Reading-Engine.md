# The reading engine

**Vendored, not written here.** This document and the code it describes came from
Vidhye (`87d1df6`), the oral reading tutor built against the same YC RFS as The
Primer. `Docs/Build/Reading-Integration.md` is the programme brief that governs
how it is being folded in, and §1.3 there explains why these are one product and
not two.

The code lives at `backend/apps/reading/engine/`, copied **unchanged** apart from
one documented edit (lazy model imports in `detection/__init__.py`, so the pure
half is importable on a box with no torch installed). Its tests are at
`backend/apps/reading/tests/` and are new — the engine shipped without any.

**Everything below is a measurement, not a design intention.** That is the reason
this file was carried across rather than summarised: the findings are the asset,
and re-deriving them costs real time and a GPU. Two caveats that apply to every
number on this page, and are restated at the bottom:

- **No child has ever used this.** Every fixture is an adult imitating one, and
  every threshold here was tuned against that voice. Phase 1 of the programme
  exists to fix precisely this; Phase 2 is where these numbers get re-measured.
- **The latency figures are from a CPU laptop**, not from the deployment.

Where a claim below has been superseded by something in this repo, it is marked
inline. Nothing has been quietly edited to agree with us.

---

## How the engine works

Six stages, every time a child speaks:

```
audio → detection → attribution → mastery → teaching → comprehension → selection
        (what went   (which        (how are  (how do    (did they      (what to
         wrong)       skill)        they      we help)   understand     read next)
                                    doing)               it)
```

**The teaching stage is the one that makes this a tutor.** Without it the loop
only ever tests: the child performs, we grade, we hint, we move on. A tutor
sitting beside a child spends most of their time doing the opposite — reading to
them, breaking words apart, saying them together. Section 5 is that.

---

## 1. Detection — what went wrong

Two engines run on the same audio. They answer different questions, and we need
both.

### Lane A: Whisper — *what* was said

`backend/apps/reading/engine/detection/whisper_engine.py`

Transcribes with word-level timestamps and confidence, then diffs the result
against the passage we already know they were reading. That diff produces one
verdict per word:

| Verdict | Meaning |
|---|---|
| `ok` | read correctly |
| `substitution` | said a different word |
| `omission` | skipped it |
| `extra` | said something not in the passage (stutter, filler, self-correction) |
| `unclear` | matched, but the engine wasn't confident |

Plus two timing flags: `slow` (word took 3× the clip's median) and
`paused_before` (long silence first).

**Why `base` and not a bigger model.** Measured on our fixtures, `base` is 3×
faster *and* catches more of what matters. A bigger model has stronger language
priors, so it "helpfully" corrects a child's mispronunciation back into the word
it expected — `small` turned a spoken "sip" back into "ship". We already know
the target text, so that prior is a liability.

**Why `unclear` is never treated as an error.** It comes from the confidence
score, which we measured as unreliable — it missed a real error at 0.92 and
flagged correctly-read words at 0.19. In practice it fires on accent. A child
saying "paath" for "path" has decoded correctly, and correcting them teaches
that their accent is wrong. So `unclear` never triggers coaching, never fails a
retry, and never counts against mastery.

### Lane B: Phonemes — *how* it was pronounced

`backend/apps/reading/engine/detection/phoneme_engine.py`

Whisper can say a word came out wrong. It can't say *which sound* broke, and it
misses mispronunciations entirely when the result isn't a real word. This lane
compares the sounds the child produced against the sounds the word should
contain.

- Expected sounds come from `espeak` (a pronunciation dictionary).
- Heard sounds come from `wav2vec2`, an acoustic model that outputs phonemes
  directly rather than words.
- The two sequences are aligned, and differences are filtered through
  `detection/phonemes.py`.

**That filter is the important part.** Most differences aren't misreadings:

- Dictionary and acoustic model disagree constantly about vowels — "has" is
  written /hɐz/ and heard /hæz/. Same word, same pronunciation.
- Dialects differ. Indian English realises *th* as a dental stop. That's correct
  decoding, not an error.

So sounds are grouped into equivalence sets, and only differences *across*
groups count. Tested against our recorded fixtures: 13 of 14 judged correctly.

**Two deliberate limits:**

1. Vowel swaps are tolerated, so this lane won't catch "pat" read as "pot". It
   doesn't need to — that makes a different real word, so Lane A sees it as a
   plain substitution.
2. Only words that exercise a phonics skill get sound-scored. Function words
   ("a", "the", "on") reduce in connected speech and would generate noise we'd
   never act on.

**It refines, it never triggers.** A word Lane A called fine is never
overridden. This lane has a real false-positive rate — a mid-word hesitation
reads as a dropped sound — and correcting a child who read correctly costs more
than missing an error. Used this way, a false positive can at worst make an
existing correction's wording less apt. It cannot invent a correction.

---

## 2. Skill attribution — which phonics skill was that?

`backend/apps/reading/engine/content/skills.py`

Ten skills, ordered by the UFLI Foundations sequence (a free, widely-used
science-of-reading scope and sequence), each tagged with its Common Core code.

Words map to skills by **spelling pattern**, not by hand-tagging:

| Skill | Pattern | Matches |
|---|---|---|
| `digraph_sh` | contains `sh` | ship, wash, fish |
| `digraph_th` | contains `th` | path, this, bath |
| `blends_s` | starts `sc/sk/sm/sn/sp/st/sw` | stop, swim |
| `vce_long_a` | `a_e` at the end | cake, grape |
| `cvc_short_a` | consonant-a-consonant | cat, mat |

Checked most-specific-first, so "ship" matches the *sh* digraph rather than
short *i*.

**Sight words are excluded.** "the" matches the *th* pattern but is taught by
memorisation — its vowel is irregular. Stopping a child to sound it out teaches
the wrong thing. Decodable *th* words like "this" and "path" are still targets.

---

## 3. Mastery — how are they doing?

`backend/apps/reading/engine/tutoring/mastery.py`

One `SkillState` per skill **per child** — it accumulates over weeks, which is
what lets the tutor grow with them rather than start cold each session. It holds
`attempts`, `errors`, `consecutive_clean`, and the set of *distinct words* read
correctly.

Status is derived, not stored:

| Status | Condition |
|---|---|
| `unseen` | never attempted |
| `shaky` | wrong more often than right |
| `mastered` | ≥10 readings, ≥4 distinct words, ≤30% wrong, **and** a recent streak |
| `practicing` | anything else |

**Mastery is deliberately hard to claim**, and the distinct-word requirement is
the part that matters. A streak alone used to be enough, which meant one clean
read of *"She will wash the dish"* — three *sh* words in a sentence — marked the
pattern mastered. Ten clean reads of the same two words says the child has
memorised those words, not that they can decode the pattern; *sh* at the start of
a word is a genuinely different difficulty from *sh* at the end.

The poor-record check runs first, so no streak can paper over a bad record. One
error resets the streak.

---

## 4. Selection — what to read next

`backend/apps/reading/engine/tutoring/selection.py` decides **what to practise**;
`backend/tutor/services.py` decides **which text**.

That split matters. The engine can't see the passage library or what the child
has already read, so when it also chose the passage it kept returning the first
match from a hardcoded list — the same sentence, over and over, however well the
child read it.

### Step 1 — what should they practise?

Any skill they've **got wrong and not yet mastered**. Waiting for a skill to look
statistically bad before revisiting it would mean ignoring the mistake they just
made, which is the opposite of what a tutor does.

Ranked by how *often* they get it wrong, not how many times — 5 wrong out of 16
is less urgent than 4 wrong out of 4, even though it's more errors. The rate is
smoothed so a single slip on a brand new skill doesn't read as total failure:
one-wrong-out-of-one scores 0.67, below a consistent 0-for-3 at 0.80.

If nothing is weak, walk the UFLI sequence and take the first skill not yet
mastered — so a child reading cleanly advances through the curriculum in order.

### Step 2 — which passage?

Never one they've already read this session. A child who reads something
correctly and is handed it straight back learns that nothing they do matters.

Generated passages are preferred over curated ones, so the built-in thirteen act
as a floor rather than the whole experience. If a skill has no unread passages
left, anything unread beats a repeat.

> **Two bugs worth knowing about.** Selection originally only considered skills
> marked `shaky` — more than half wrong — so a child who missed a *sh* word once
> was ignored entirely. And the "already read" filter compared database integer
> ids against curated string ids like `"p001"`, so it never matched anything.
> Both are fixed; both were invisible until someone read the same sentence ten
> times in a row.

### Worked example

Child reads *"Sam has a big fish."* and says "wish" for "fish".

1. **Detection** — Lane A: `fish` → `substitution`. Lane B: missed `/f/`.
2. **Attribution** — "fish" contains `sh` → `digraph_sh`. Also credited: "Sam",
   "has" (short a), "big" (short i).
3. **Mastery** — `digraph_sh`: 1 attempt, 1 error, `practicing`. Short a: 2/2,
   heading toward mastered.
4. **Teaching** — tutor stops on "fish". They've read `sh` correctly before, so
   it starts at a nudge rather than modelling the whole word.
5. **Selection** — `digraph_sh` has an error and isn't mastered → weakest skill.
   First unread passage tagged `digraph_sh` is *"The ship is on the shelf."*

Had the child read it cleanly, step 5 would instead walk the sequence and pick
whatever comes next that isn't mastered.

---

## 5. Teaching — how the tutor helps

`backend/apps/reading/engine/tutoring/teaching.py`, `rapport.py`

### Modelling: read it to them first

Before a passage, the tutor decides whether to **read it aloud first** or hand it
straight over. That single decision is what separates teaching from testing, and
it's adaptive on *method* rather than content — the same passage is delivered
differently to different children.

It reads first when the child is struggling, or when the passage contains a sound
they've never met. It stays quiet when they're reading well; over-helping a
fluent child wastes their time and is faintly insulting.

### The ladder: a different kind of help each time

A failed word used to get the same hint twice and then a shrug. Now each attempt
gets a genuinely different teaching move, each asking less of the child than the
last — the gradual-release sequence any reading teacher would recognise:

| Rung | What the tutor does |
|---|---|
| `nudge` | Names the sound, asks them to try — *"One word. This one has the sh sound. ship. Your turn."* |
| `model` | Says the whole word to echo — *"Listen. ship. Now you say it."* |
| `segment` | Breaks it into graphemes and blends — *"sh. i. p. Now put it together."* |
| `together` | Says it in unison, no solo attempt expected |
| `release` | Lets it go warmly |

**Where a child enters the ladder is adaptive.** One who has read this sound
correctly before gets a nudge; one who is reading word-by-word is already at
their limit and gets the word given to them outright.

Segmentation uses graphemes, not letters (`backend/apps/reading/engine/content/graphemes.py`)
— "sh" is one chunk, because a child taught to say "ess-aitch-i-p" has been
taught something actively wrong. Silent e is named rather than sounded.

### Reading the room

`rapport.py` tracks how the last few minutes have gone — clean streak, struggle
streak, how many readings were laboured. Mastery says what a child knows over
weeks; this says whether right now is going well, and they drive different
decisions.

After three straight struggles the tutor starts modelling. After five it **ends
the session early and warmly**. A child who has failed five passages running has
stopped learning and started failing, and leaving them willing to come back
matters more than one more attempt.

### The shape of it

```
begin passage
  ├── struggling or new sound → tutor reads it first, child echoes
  └── flowing → straight to the child

read
  ├── clean → praise (+ a streak note when earned) → next passage
  └── error → pick ONE word → climb down the ladder
        ├── correct → "that's it" → reread the sentence → confirm → next
        └── wrong → next rung down
              └── after `together` → reread → next
```

**Only one word is taught**, even when several were wrong. A tutor doesn't recite
every mistake; they pick the one worth teaching. Preference goes to words that
exercise a phonics skill (a missed "ship" over a missed "the").

**Retries are judged generously.** A one-word clip gives the engine little to
work with, so anything recognisable passes. Falsely telling a child they failed
costs far more than letting a shaky attempt through.

---

## 6. Fluency

`backend/apps/reading/engine/detection/fluency.py`

Accuracy can't see the difference between a child who is reading and one who is
grinding out a word at a time. Words correct per minute can, and it's the measure
every reading specialist already uses — which means it's both the right signal
and a number that means something outside this app.

Timed from the first word actually spoken to the last, so a child who takes a
breath before starting isn't marked slower for it. Suppressed entirely below
three words or 1.5 seconds; a rate from that would be noise dressed as a number.

It feeds two things: the parent's view, in plain language rather than a score,
and the teaching ladder — a laboured reader is given the word rather than asked
to work harder.

---

## 7. Comprehension — did they understand it?

`backend/apps/reading/engine/tutoring/comprehension.py`

Everything above measures decoding: whether the sounds on the page came out of
the child's mouth. A child can pass all of it and have taken nothing from the
sentence. Reading is decoding *and* meaning.

After a clean read, the tutor asks one literal question about the sentence —
*"What does Sam have?"* — and listens for a word.

**Why this is checkable at all.** Judging a free-form answer would need a
language model, which is slow and fallible. But a literal question about a
seven-word sentence has an answer that is *a word from that sentence*. So we
know what we're listening for, which is exactly the situation the detection
engine already handles. Every generated question is validated on that property:
if the model's answer isn't in the text, the question is discarded rather than
served.

**Only after a clean read.** A child who just fought through the decoding had
all their attention on the words; asking what it meant tests something they had
no capacity left for, and failing it teaches them reading is a series of tests
they lose.

**Never drilled.** A wrong answer is told the answer and the tutor moves on.
Comprehension isn't fixed by being asked again harder — it's fixed by reading
more with someone pointing things out. A second attempt would only add a way to
fail.

**Checking is deliberately loose.** "cat", "the cat", "it was the cat" and
"cats" all pass. We're testing whether they understood, not how they phrase it.

Tracked separately from phonics, because the two fail for different reasons and
need different help. A child who decodes every word and answers nothing is a
specific, common problem that a single reading score would hide.

---

## 8. What the tutor says

`backend/apps/reading/engine/tutoring/coaching.py` and `brief.py`

A `Brief` carries the *facts* behind one piece of speech: the word, the sound,
the rule, whether they've missed this sound before, whether they're otherwise
reading well. `coaching.say()` turns a Brief into words.

Templates render it today. Swapping in a language model means replacing that one
function — the Brief already carries the context a model would need.

**The split is deliberate.** Phonics has right and wrong answers. A model asked
to invent instruction will occasionally invent it wrongly, invisibly, to a child
who can't tell. So the model gets handed the facts and writes only the delivery;
it never decides what's true about the language.

---

## Speed

The two lanes run **concurrently** — they answer independent questions, so the
wait is the slower of the two rather than their sum. Measured, the phoneme lane
now finishes entirely inside Whisper's shadow and adds no time at all.

Audio is decoded once and passed to both as an array. Expected phonemes are
cached, since what a sentence *should* sound like never changes.

What's left is Whisper, and its cost is unusual: it always encodes a 30-second
window, padding short clips to fill it, so a child's three-second sentence costs
the same as a thirty-second one. On CPU that fixed term is ~5s and dominates
everything; on GPU it's what collapses. `ASR_DEVICE` and `ASR_COMPUTE_TYPE`
switch both models over without a code change.

`engine/detection` logs its own breakdown on every analysis, so this is a
measurement rather than an estimate.

---

## Known gaps

- **Never tested with a real child.** Every fixture is an adult imitating one.
  This is the biggest hole in everything above, and every threshold on this page
  was tuned against the wrong voice.
- **Per-child calibration** would replace the hand-written accent tolerance
  table: learn how *this* child says each sound, then flag only deviations from
  their own baseline.
- **Position-aware skills.** *sh* at the start of a word and *sh* at the end are
  different difficulties, and we collapse them into one counter. `WordOutcome`
  already stores the evidence; nothing uses it yet.
- Mid-word hesitations still read as dropped sounds in Lane B.
- Passages already read are only excluded **within** a session, so an old
  passage can reappear in a new one.

---

## Measured limitations

Carried across from Vidhye's `LIMITATIONS.md` (5 August 2026), ordered by how
much they would matter to someone evaluating this. Written to be honest rather
than flattering.

### Latency, and why it is a hosting problem

From a child finishing a sentence to hearing the tutor, on a 3-second clip:

| | Time |
|---|---|
| First read after the worker starts | ~8s |
| Steady state — median | **5.6s** |
| Steady state — best | 4.9s |
| Worker startup (models loading) | ~50s, once |

Where it goes:

| Stage | Cost |
|---|---|
| Decode audio | 0.35–1.7s |
| **Whisper** | **4.3–5.6s** — effectively all of it |
| Phoneme lane | **+0.00s** — concurrent, finishes inside Whisper's shadow |
| Queue, DB writes, polling | ~0.3s |

**Whisper always encodes a 30-second window**, padding shorter clips to fill it,
so a 3-second sentence costs what a 30-second one does. Measured: 3s → ~5s,
12.3s → ~10.4s. A large fixed term plus a small variable one. That fixed term is
architectural to Whisper and is what a GPU removes.

`ASR_DEVICE=cuda ASR_COMPUTE_TYPE=float16` needs no code change. **This was never
measured** — there was no GPU — so treat the sub-second figure as an expectation.
Phase 2 of the programme brief is where it becomes a number.

Tried and rejected: `whisper tiny` (halves it, but reports "ship" as *unclear*
instead of catching "sip" as a substitution — misses the exact error the product
exists to catch); `whisper small` (3× slower *and* less accurate here); disabling
the phoneme lane (saves no time, since the lanes are concurrent — it is a 1.2GB
memory lever, not a speed one).

### Memory

| Service | Resident |
|---|---|
| Celery worker with both models | **1553 MB** |
| Django | 111 MB |
| Postgres + Redis | ~250 MB |

This is the measurement behind §2.1 of the programme brief. The pilot box is a
4GB `e2-medium` already running Postgres, Redis, uvicorn and a prefork worker.

### What is proven and what is not

Measured on two fixtures with deliberately planted errors
(`backend/apps/reading/research/`):

- Omissions, substitutions, self-corrections, hesitations — caught reliably
- `ship` → `sip` digraph error — caught precisely at the phoneme level
- Accent tolerance — 13 of 14 judged correctly

Not proven:

- **Vowel swaps are deliberately tolerated**, so the phoneme lane will not catch
  "pat" read as "pot". The word-level lane sees it as a substitution because it
  makes a different real word, so it is not lost — but the precise lane misses it.
- **Mid-word hesitations read as dropped sounds** in the phoneme lane. A known
  false positive, and the reason that lane only ever refines a correction and
  never raises one.
- **`unclear` never triggers coaching.** Whisper's confidence proved unreliable —
  missed a real error at 0.92, flagged correctly-read words at 0.19, and gave
  different numbers on identical audio between runs. In practice it fires on
  accent. The cost is that some genuine mispronunciations pass silently. That is
  the deliberate trade: correcting a child who read correctly is worse than
  missing an error.

### Skills are tracked too coarsely

Every *sh* word feeds one counter. But `ship` (start), `wash` (end) and `shelf`
(before a blend) are genuinely different difficulties, and `she` is probably
known by sight rather than decoded. The per-word evidence to fix this is already
recorded; nothing uses it.

### Operational fragility

- **`av` and torch segfault** when the audio libraries are entered from two
  places in one process. Centralising decoding fixed the observed crashes; the
  underlying conflict remains.
- **Single ML worker, concurrency 1.** Correct for the memory profile, but one
  slow analysis blocks the next.
- No retry if the worker dies mid-analysis. The task times out at 120s.

---

## What was left behind

Vidhye's Django and Next.js layers did not come across — this repo has better
versions of both. Specifically **not** vendored, and worth knowing exists if a
later phase wants it:

| Left in Vidhye | Why | Wanted by |
|---|---|---|
| `tutor/validation.py` | Decodability validation for generated text | Phase 4 |
| `tutor/generation.py` | Passage generation via OpenRouter | Phase 4, if at all — this repo generates through its own router |
| `tutor/speech.py` | Piper TTS with a disk cache | Nothing — `apps/media/adapters/tts.py` is better |
| `tutor/models.py` | `SkillState`, `WordOutcome`, `Session` | Phase 2, as a guide for what evidence to persist |
| `frontend/lib/speech.ts` | `MediaRecorder` + silence detection | Phase 1, as a guide — note its 1.5s silence threshold was tuned on adults and **will cut a child off** mid-word |
