# Reading Integration — bringing the Vidhye engine into The Primer

**Programme brief.** Five phases, each a self-contained build session in the style of
`Docs/Build/DayN.md`. Written 15 August 2026, after the 15-day sprint and the
cleanup pass, against The Primer at `f6b6862` and Vidhye at `87d1df6`.

This file is the **programme-level** brief: the why, the constraints, the phase
boundaries and the acceptance criteria. Each phase gets its own build session.
Hand a session *this file* plus the phase you want built.

> **Source-of-truth rule, unchanged.** `Docs/PRODUCT.md` wins for architecture;
> this brief wins for scope of the phase. Where this brief and PRODUCT.md
> disagree about what The Primer *is*, PRODUCT.md is right and this brief is
> wrong — say so rather than improvising.

---

## 1. Context — why this is happening

### 1.1 What we are merging in

`C:\Users\DELL\OneDrive\Desktop\ClaudeCode\Vidhye` holds an adaptive oral reading
tutor for ages 5–7, ~6k lines, working end to end. A child reads a sentence
aloud; it identifies the specific *sound* that broke, speaks a correction, hears
the retry, and picks the next passage from what they got wrong.

Its engine is six stages: `detection → attribution → mastery → teaching →
comprehension → selection`. The parts that matter here:

| Component | What it does |
|---|---|
| `engine/detection/whisper_engine.py` | Whisper `base` + known-text alignment → per-word verdict (`ok` / `substitution` / `omission` / `extra` / `unclear`) plus `slow` / `paused_before` |
| `engine/detection/phoneme_engine.py` | wav2vec2 phonemes vs espeak expected → *which sound* broke |
| `engine/detection/phonemes.py` | Accent-tolerance equivalence groups. 13 of 14 judged correctly |
| `engine/content/skills.py` | 10 UFLI-ordered phonics skills, matched to words by spelling regex, Common Core RF codes attached |
| `engine/tutoring/mastery.py` | Per-child per-skill state; mastery needs ≥10 readings **and ≥4 distinct words** |
| `engine/tutoring/teaching.py` | 5-rung gradual-release ladder; entry rung is adaptive |
| `engine/tutoring/comprehension.py` | One literal question after a clean read, checked without a language model |
| `tutor/validation.py` | Decodability validation for generated text |

**The engine is pure Python** — no Django, no HTTP, no ORM. It survived a
FastAPI→Django migration completely unchanged. That property is the reason this
integration is cheap, and preserving it is a hard rule below.

### 1.2 Why it belongs here

PRODUCT.md calls The Primer a *"personalized lifelong learning companion for
children aged 3–7"*. Today it stops at exactly the age where reading starts. A
four-year-old is read to; a seven-year-old wants to read. The product currently
has one answer for both.

The arc this unlocks: **at three the companion reads everything; at five, "read
this line with me"; at seven the child reads the page and the companion listens.**
That is the adaptive claim actually delivered, and a content library cannot make
it.

### 1.3 This is one product, not two

Stated here because it governs how the work is described, not just done.

- Vidhye's own README says it was *"built against YC's 'The Primer' request for
  startups"* — the same RFS this repo is built against. Two runs at one brief,
  never two companies.
- The RFS is a Diamond Age reference, and the Young Lady's Illustrated Primer is
  a storybook that teaches Nell to read *through story*. A standalone reading
  drill is a **subset** of that brief. A storybook that also teaches reading **is**
  the brief.
- YC Update 1 claimed the detector and the ASR finding. Every word of it stays
  true — the finding ("word-level ASR is structurally blind to mispronunciation")
  is a fact about ASR, not about product shape. Nothing is retracted by this work.

The open question Update 1 left is *why would a five-year-old open this
tomorrow?* The story engine is the answer to that question. Technical risk
first, engagement layer second, is the correct order — and it means there was
only ever one product.

### 1.4 The framing that decides scope: evidence, not features

Vidhye's own `TWENTY_DAYS.md` sets the test: *"does this produce evidence, or
does it produce features? If features, don't."*

**Phase 1 is evidence-producing** — it yields a corpus of real 5–7-year-old
speech, which both codebases name as their single largest gap, and it produces a
retention signal. **Phases 2–4 are features** and should sit behind real usage.

The programme is deliberately ordered so the evidence phase is also the cheapest
and least risky one.

---

## 2. The three hard constraints

Each is a measured fact, not a worry. Each is fatal alone.

### 2.1 The box is 4 GB

The pilot runs on a single GCE `e2-medium` (4 GB) with Postgres, Redis, uvicorn
and a prefork Celery worker at concurrency 2 all on it (`Docs/Hosting.md §7`,
`prod/15-08.txt`). Whisper base + wav2vec2 measure **1.4 GB resident, per
process** (Vidhye `LIMITATIONS.md`).

> **Rule: the speech models never run on the app box.** Not in the web process,
> not in the existing Celery worker, not "just for testing". This decides the
> shape of everything below.

### 2.2 Six seconds is not a beat

Measured median **5.6 s** from the child finishing to the tutor speaking, and it
barely varies with clip length — Whisper always encodes a fixed 30-second window,
so a 3-second sentence costs what a 30-second one does. That fixed term is the
whole cost and only a GPU removes it.

The Primer's contract is acknowledgement inside ~100 ms and a standing rule that
*the child never sees a raw error or a spinner*. A six-second silence after a
child reads aloud violates it.

### 2.3 The detector has never heard a child

Every threshold — silence timing, confidence cutoffs, duration multipliers,
phoneme tolerance — was tuned against an adult imitating a five-year-old.

Vidhye ranks the resulting failure first by damage: **correcting a child who read
correctly.** *"Teaches them to distrust the tutor, and it's unrecoverable."*

Inside The Primer this is worse than it was in Vidhye, because the thing they
would learn to distrust is **the companion** — the relationship every other
feature is built on. Live correction on an untuned detector is not shippable
here at any speed.

---

## 3. The architectural principle

All three constraints are constraints on **scoring**. None of them is a
constraint on a child reading aloud.

> **Separate the interaction from the scoring.** Vidhye could not — scoring *was*
> the app. The Primer can.

- The **interaction** — companion invites, child reads a line out loud, companion
  responds warmly, parent sees it happened — needs no GPU, no accuracy, and
  contains nothing that can be wrong about a child.
- The **scoring** — which sound broke, mastery over weeks, adaptive selection —
  needs the separate host and needs tuning against real children.

Split them and the ordering solves itself: the first produces exactly what the
second is missing.

---

## 4. Where reading sits in an adventure

**Correcting a natural assumption before anyone builds on it.**

`orchestrator.learning_seqs()` gives every adventure **exactly two** ask slots —
the beat after the opening, and the beat before the close. Its docstring is
explicit about the ceiling:

> *"Not more than two, because the ratio is the invisibility. A five-beat story
> with four asks in it is a worksheet with pictures, whatever the wording of the
> beats (Principle #1)."*

So reading does **not** get a third slot. It **competes for the two that already
exist**, as a third learning domain.

The selector already interleaves across domains (`INTERLEAVE_PENALTY`,
`_last_domain` in `learning/engine.py`), so a five-beat adventure will naturally
land roughly one numeracy ask and one literacy ask on many days — chosen by ZPD,
due-date and interleaving rather than hardcoded, and without touching the
invisibility ratio.

**For Phase 1 only**, where reading must appear reliably enough to gather
evidence, pin literacy to one of the two slots behind an explicit, temporary,
removable pilot flag. **Start at one reading beat per adventure, not two** — we
are measuring whether a child will do it at all, and two novel demands in one
sitting confounds that measurement.

---

## 5. Cross-cutting rules — every phase

These hold from Phase 0 to Phase 4 and are not re-argued in each phase.

1. **The engine stays framework-free.** `apps/reading/engine/` imports no Django,
   no DRF, no models. Adapters around it may. This is the property that made the
   port cheap and it is trivially lost by one convenient import.
2. **Audio discipline is imported wholesale from Vidhye.** Deleted the moment it
   is handled; never in the database; never in a backup; never logged. Retention
   is opt-in per session, consented by a grown-up each time, never inherited
   between sessions, and written outside the repo.
3. **No child under 5 is ever asked to read.** Gate on `Child.age_band`
   (`profiles/models.py:89`, splits at 4/5+), a parent switch, and
   `words_on_screen` being on — a child cannot read a line that is not displayed.
4. **Reading errors never enter the memory system.** `memory/capture.py` already
   treats `say` utterances as memory candidates (`_UTTERANCE_TYPES`). Capture
   *that reading happened*, never *how it went*. "The child said 'sip' instead of
   'ship'" must never become a remembered fact about them.
5. **Every child utterance goes through `safety.guard_child_utterance()`**
   (`safety/pipeline.py:223`). It was written for "every point a child's words
   enter the system"; this is a new such point.
6. **No new AI vendor.** Do not reach for a cloud speech API. Vidhye's central
   measurement is that a stronger language prior *erases* the errors we hunt —
   Whisper `small` turned a spoken "sip" back into "ship". A production STT
   service has a stronger prior still and cannot do phonemes at all. It is not a
   shortcut; it is the wrong instrument.
7. **The model writes the delivery; the engine owns what is true about the
   language.** Phonics has right answers. A model asked to invent instruction
   invents it wrongly, invisibly, to a child who cannot tell. Same split the
   product already uses everywhere.
8. **Every phase ends deployable**: migrations applied, `make lint` and
   `make test` green, CI green.

---

## 6. What already fits

Found by reading the code, not assumed. Each is a socket already cut.

| Seam | Where | Note |
|---|---|---|
| `InteractionType.SAY` | `story/ai/schemas.py:106` | Already in the closed enum the model generates against, and **nothing handles it** — a `say` beat falls through to the generic "Let's go →" button at `StoryPage.tsx:579` |
| `guard_child_utterance()` | `safety/pipeline.py:223` | Written for every ingress of child speech. Covers this one by design |
| `Domain` TextChoices | `learning/models.py:35` | Docstring: other domains "are added by extending this, one migration at a time" |
| `Milestone.standard_ref` | `learning/models.py:76` | Open JSON field. Vidhye's Common Core RF codes drop in |
| `Demand` / `.summary()` | `learning/demands.py:135` | Turns a DB fact into a story constraint. A read demand is the same shape |
| Adapter Protocol + failover | `media/adapters/` | Image, TTS, embedding. ASR becomes the fourth and inherits breaker + budget |
| Scaffold-not-mark | `StoryPage.tsx` `handleCount` | "A hand rather than a mark" — the teaching ladder, arrived at independently |
| `age_band` | `profiles/models.py:89` | Splits at 4/5+. Vidhye targets 5–7 |
| Prefetch + staged media | `orchestrator.py:2990` | Vidhye's "cheapest remaining win, not yet built" is already how this product works |

---

## 7. Phase 0 — Seams

**Move the engine in and change nothing observable.**

### Scope

- Vendor Vidhye's `backend/engine/` into `backend/apps/reading/engine/`
  **unchanged**. Copy, do not rewrite. If a file needs editing to import, the
  import is wrong, not the file.
- Bring `ENGINE.md` and `LIMITATIONS.md` across as `Docs/Reading-Engine.md`. The
  measured findings are the asset — the Whisper-corrects-Den finding, the
  confidence-score unreliability, the accent tolerance results, the latency
  breakdown. Losing them means re-deriving them from scratch at real cost.
- Copy the research fixtures (`research/asr_spikes/fixtures/`) so the tuning work
  in Phase 2 has a baseline to compare against.
- **Give the engine the test suite it never had.** It is pure functions over
  fixtures — the easiest thing in either repo to test, and this repo's CI
  discipline requires it. Cover at minimum: `skills.skill_for_word` (including
  sight-word exclusion and match ordering), `mastery.status` (all four states,
  including the distinct-word requirement), `teaching.first_rung` / `next_rung`,
  `graphemes.sound_out`, `comprehension.check`.
- Add `Domain.LITERACY` and a migration.
- Seed the ten UFLI objectives as `Objective` rows with RF codes in
  `standard_ref`, ordered by `ufli_order`, with `prerequisites` following the
  sequence.
- Add a `READ` demand kind to `learning/demands.py` with `kind_for` dispatch. It
  produces a `Demand` but nothing selects it yet.
- Add an `asr` adapter Protocol beside `image` / `tts` / `embedding`, with a null
  implementation that raises if called.

### Acceptance criteria

- `apps/reading/engine/` contains no import of Django, DRF, or `apps.*`. Assert
  this in a test — it is the property most likely to be lost by accident.
- New engine tests pass; overall coverage does not drop.
- Migrations apply cleanly; `Domain.LITERACY` exists; ten objectives seeded.
- **Product behaviour is byte-identical.** No child-facing change whatsoever.
- `make lint`, `make test`, CI green.

### Do NOT do in Phase 0

- No microphone, no upload endpoint, no UI.
- No ML model loading anywhere, including tests — the engine tests run on
  fixtures and pure functions only.
- Do not touch `orchestrator.py`.
- Do not wire literacy into the objective selector.

### As built — 15 August 2026

Done, with two things worth knowing that the brief above did not anticipate.

| What | Where |
|---|---|
| Engine, vendored | `backend/apps/reading/engine/` |
| Findings + limitations | `Docs/Reading-Engine.md` |
| Research fixtures + ASR spike findings | `backend/apps/reading/research/` |
| Tests (new) | `backend/apps/reading/tests/` — isolation, skills, mastery, teaching, comprehension, catalog join |
| Literacy domain | `learning/models.py` `Domain.LITERACY` + `0003` |
| Ten UFLI objectives | `learning/migrations/0004_seed_literacy.py` |
| `READ` demand kind + ladder | `learning/demands.py` |
| ASR adapter seam | `apps/media/adapters/asr.py` |

**Deviation 1 — one file was edited, not vendored verbatim.**
`engine/detection/__init__.py` imported faster-whisper, torch and transformers at
module scope. That was correct in Vidhye, where only the ML worker imported it,
and wrong here: §2.1 keeps those models off this box entirely, so importing the
*pure* half of the engine must not drag them in. The three model-backed imports
moved inside the functions that use them. Every public name still resolves to the
same object. `test_engine_isolation.py` asserts both halves of this — no
framework imports anywhere, no heavy imports at module scope outside the three
model-backed modules, and a subprocess check that a fresh interpreter importing
the engine loads none of them.

**Deviation 2 — a selection gate the brief did not call for.**
Seeding literacy made the objectives reachable by `select_objective`, because
their `min_age` of 5 is inside a five-year-old's band. An existing test
(`test_catalog.test_no_domain_beyond_numeracy_and_vocabulary_has_been_seeded`)
caught it, and its rationale was exactly right: *an objective that can be
selected and cannot be taught*. There is no story rule for a `READ` demand yet,
so a selected literacy objective would have become a beat that quietly practised
nothing.

`engine.SELECTABLE_DOMAINS` is the fix — the selector filters on it, and it
excludes literacy until Phase 4 opens it. The old test was rewritten to assert
what is now true (seeded, not selectable) rather than deleted, and a second test
exercises the gate by selecting twenty times for a five-year-old and asserting no
phonics objective ever comes back.

**Also not in the original brief:** the vendored engine is excluded from ruff and
mypy (`backend/pyproject.toml`, both with reasons written in). Restyling 2,000
lines of vendored code would destroy the diff against upstream, which is the main
reason to vendor rather than rewrite. It earns its place through tests instead.

Verified: `ruff check` clean, `ruff format --check` clean, `mypy` clean across
278 files, `makemigrations --check` reports no drift, reading and learning suites
green.

### End state to hand to Phase 1

One repo, one history. The engine is present, tested, framework-free and inert.
Literacy exists as a domain with ten objectives, is visible to a curriculum
reader and to any offline analysis, and **cannot be selected**.

---

## 8. Phase 1 — Invite

**The child reads aloud; the companion listens warmly. No ML in the child's path.**

This is the phase that produces the evidence. It is also the entire visible
glow-up for the pilot.

### Scope

- Implement the `READ` interaction end to end (extend `InteractionType`, or use
  the unused `SAY` — decide in the phase brief; `READ` is preferred because the
  UI, the expected value and the evidence all differ from a one-word `say`).
- `MediaRecorder` in the PWA with an explicit, child-legible affordance. Port the
  silence-detection tuning notes from Vidhye's `PLAN.md`: the 1.5 s threshold was
  tuned on adult reading and **will cut a child off** mid-word — a child sounding
  out "sh-i-p" pauses three or four seconds. Raise the threshold and the ceiling.
- Upload endpoint. Audio written to a path the analysis side can read, deleted
  the moment it is handled.
- Opt-in retention with per-session grown-up consent, stored outside the repo.
- **The companion responds warmly and unconditionally.** No correction, no
  scoring, no judgement. Evidence recorded is `read_attempted`, never
  `read_correctly`.
- Gate on age band, parent switch, and `words_on_screen`.
- A "say it again" affordance — Vidhye's field note is that children ask for
  repetition constantly and there is no way to replay the companion.
- Parent dashboard: one honest number, *lines read aloud this week*.
- Consented recordings land in a corpus with the passage text, the child's age,
  and nothing else identifying.

### Acceptance criteria

- A child on the older band gets one reading beat per adventure and can complete
  it end to end.
- A child on the younger band never sees one. Assert in a test.
- With retention off, no audio survives the request. Assert in a test.
- With retention on and consent given, the file lands in the corpus path.
- The utterance passes through `guard_child_utterance`.
- Reading outcomes do not appear in memory candidates. Assert in a test.
- Nothing in the child's path loads a speech model.
- `make lint`, `make test`, CI green.

### Do NOT do in Phase 1

- **No scoring, no transcription, no correction.** Not even "quietly, to see how
  it does". The whole safety argument of this phase is that there is nothing to
  be wrong about.
- No GPU, no new service, no model in any process.
- Do not touch `orchestrator.py` beyond what the interaction type requires.
- No mastery updates from reading.

### As built — 15 August 2026

| What | Where |
|---|---|
| The moment | `frontend/src/routes/child/ReadingMoment.tsx`, on the warm close |
| Recorder | `frontend/src/lib/recorder.ts` |
| API | `GET`/`POST /api/v1/children/{id}/reading` — `apps/reading/views.py` |
| Rules | `apps/reading/service.py` — gates, line choice, consent |
| Corpus row | `apps/reading/models.py` `ReadingAttempt` |
| Consent | `Child.reading_audio_consent`, off by default |
| Parent view | `linesRead` on the weekly report |

**Where it sits, and why not in a beat.** On the warm close, after the story has
resolved and above the "Again!" / "All done" buttons rather than instead of them.
That position does three things a beat-level insert could not: it interrupts
nothing, it lets a child decline by simply tapping past it — the only kind of
refusal a five-year-old should have to perform — and it needs no change to
`orchestrator.py`, which §14 puts out of bounds until Phase 4.

**The line comes from the curated bank, not the story.** Thirteen hand-written
decodable sentences, already vendored with the engine. A generated line has no
decodability guarantee until Phase 4 builds the validator, and the bank has the
property the corpus needs anyway: we know exactly what the child was looking at,
which is the field Phase 2 will tune a detector against. The endpoint therefore
stores *our* text for the id, never the client's.

**Four gates, and the interesting one was already there.** `Child.pre_reader` has
been on the model since Day 1 — in the serializer, in the data export — and
nothing has ever read it to change behaviour. A parent had already told us
whether their child can read and the product had never asked. It defaults to
`True`, so silence means no. The others: `READING_ENABLED`, age ≥ 5, and
`words_on_screen`, because a line that is not displayed cannot be read.

**The silence threshold is the number to re-tune first.** Vidhye's `listen()` cut
a turn after 1.5 s of quiet, and its own plan file predicts what that does to a
child sounding out `sh-i-p`. `recorder.ts` uses four seconds plus a visible "All
done" control, and both are guesses about a child nobody has watched yet.

**Consent is by not writing.** With `reading_audio_consent` off — the default —
the upload is counted and dropped and no file is created. Vidhye wrote the file
and deleted it after analysis, which was right when something had to analyse it;
nothing does yet, so the stronger shape was available. Withdrawing consent
deletes what was kept, and so do child deletion and account deletion — the
recordings live on the box's disk, outside the media bucket, so
`delete_child_media` could never have found them.

**Deviation from the brief:** the brief listed a "say it again" control. It is not
here, because there is nothing to replay — the companion's invitation is text on
screen, not speech, and the line stays visible for as long as the child wants it.
It returns in Phase 3, where the companion speaks a correction worth hearing
twice.

Verified: `ruff`, `ruff format`, `mypy` (287 files), full backend suite, 215
frontend tests, all green.

### End state to hand to Phase 2

Real children have read lines aloud to their companion, and we have the audio.

---

## 9. Phase 2 — Score

**Analysis, entirely out of the child's path.**

### Scope

- Stand up the speech models off the app box. Because nothing is blocking,
  **this is a cost decision rather than a latency one** — a nightly batch on a
  spot VM is enough, and is the cheapest time to make the choice.
- Run the Phase 1 corpus through detection.
- **Re-tune every threshold against real 5–7-year-old speech.** This is the work
  Vidhye's `LIMITATIONS.md` ranks first and it has been waiting for Phase 1 to be
  possible. Silence timing, confidence cutoffs, duration multipliers, phoneme
  tolerance.
- Record what changed and by how much, in `Docs/Reading-Engine.md`. The delta
  between adult-tuned and child-tuned thresholds is itself a finding.
- Feed `LearningProgress` for literacy objectives from the results.
- Parent dashboard becomes real: which sounds are solid, which are shaky,
  fluency in plain language rather than a score. Keep comprehension tracked
  separately from phonics — they fail for different reasons.
- Measure actual end-to-end latency on the chosen host. "A GPU fixes it" stops
  being an expectation and becomes a number.

### Acceptance criteria

- Detection accuracy measured against real child recordings, with the numbers
  written down.
- Literacy `LearningProgress` populated for pilot children.
- Parent dashboard shows reading progress.
- Latency measured and recorded.
- Still nothing in the child's live path.

### Do NOT do in Phase 2

- No live coaching. No correction reaches a child in this phase.
- Do not put the models on the app box, even now.

### End state to hand to Phase 3

The detector is right about real children, and we can prove it on fixtures we
own.

---

## 10. Phase 3 — Coach

**The companion starts teaching. Only once Phase 2 has earned it.**

### Scope

- Turn on the live path behind a flag, for consented pilot families first.
- Map the teaching ladder onto the existing scaffold pattern — first miss buys a
  hand, not a mark — so the interaction grammar the child already knows does not
  change.
- Replace `coaching.say()` templates with the companion's own voice: `Brief` →
  the existing Vertex router carrying the companion persona → spoken line.
  Vidhye designed for exactly this swap and the `Brief` already carries the
  context a model would need.
- Hold rule 7 of §5 exactly: the model writes the delivery, the engine decides
  what is true about the language.
- Hide the remaining wait the way the product already hides waits — the companion
  has something in character to say while it thinks.
- Honour `rapport.py`: after a run of struggle, wind the reading down warmly
  rather than pressing on.

### Acceptance criteria

- A child is corrected on a real sound, in the companion's voice.
- No correction fires on `unclear` — only clear substitutions and omissions.
- The phoneme lane refines, never triggers.
- A struggling child gets the reading wound down, not more of it.
- Latency inside the beat is within the product's contract, measured.

### Do NOT do in Phase 3

- Do not let the model decide phonics facts.
- Do not enable for the younger band under any flag.

---

## 11. Phase 4 — Weave

**Reading becomes part of the story. This is the full integration.**

### Scope

- Literacy objectives enter the selector properly, interleaved with numeracy and
  vocabulary by the existing ZPD scoring. Remove the Phase 1 pilot pin.
- `Demand.summary()` for a `READ` demand asks the beat writer for one decodable
  line — *"somewhere in this adventure there is one short line the child is
  invited to read aloud, using only the sounds they know."*
- **Validate it.** Port Vidhye's `tutor/validation.py`: every word decodable from
  the child's mastered skills plus the target, or an approved sight word.
  Discard and regenerate on failure. Same "model writes, we verify" posture the
  product already takes on every generated beat.
- Only now does `orchestrator.py` get touched — and by then every other piece is
  shipped and proven.

### Acceptance criteria

- An adventure can carry a literacy ask in one of its two existing slots.
- The ask ratio stays at two per adventure. Assert in a test.
- No child is ever served a line containing a sound they have not met.
- Existing orchestrator tests still pass.

---

## 12. Open decisions

Recorded rather than assumed. Each blocks a specific phase, not the programme.

| Decision | Blocks | Default if unanswered |
|---|---|---|
| One product or two — reading inside the story, or a separate `/child/read` surface | Phase 3 | Inside the story. Phases 0–2 are identical either way, so this can be deferred about a month |
| Where the speech models run — serverless GPU vs. spot-VM batch | Phase 2 for batch, Phase 3 for live | Batch on a spot VM for Phase 2; decide the live host in Phase 3 with a measured latency number in hand |
| What happens to the Vidhye repo | Phase 0 | Archive it. The engine and findings move; its Django and Next.js layers are discarded because this repo has better versions of both. There is a case for keeping it runnable as a detection test-bench, and that case gets stronger in Phase 2 |
| Whether reading uses `SAY` or a new `READ` interaction type | Phase 1 | New `READ`. The UI, expected value and evidence all differ from a one-word `say` |

---

## 13. Deployment note — read before Phase 1

**This repo auto-deploys, as of 21 August 2026.** The paragraph that was here
said the opposite and was correct when written; `deploy.yml`'s release step was a
placeholder that echoed "Hosting target is not wired yet".

It is now real: a push to `main` runs `ci.yml` as the gate, and a green gate runs
`infra/deploy.sh` on the box over SSH — which rebuilds only what changed,
migrates before recreating, probes `/api/health/`, and reverts itself if the
probe fails. Tags still deploy too. See `Docs/Runbooks/Auto-Deploy.md`.

What has *not* changed is the gate: `release` still `needs: [gate]`, and a red
eval still means the deploy job is skipped rather than passed.

**One thing to fix before adding services.** `infra/docker-compose.yml` in the
repo defines only `db` and `redis`, but `infra/push-credentials.sh` runs
`docker compose -f infra/docker-compose.yml up -d --force-recreate web worker`
against the box — so production's `web` and `worker` definitions exist only on
that machine and are **not in version control**. That is worth resolving before
Phase 1 adds anything, and certainly before Phase 2 adds a host.

---

## 14. What not to do, at any phase

- **Do not put the speech models on the app box.** 1.4 GB into 4 GB alongside
  everything else takes the product down.
- **Do not reach for a cloud speech API to dodge the GPU.** Measured to be the
  wrong instrument (§5 rule 6), not merely a compromise.
- **Do not touch `orchestrator.py` before Phase 4.** Highest-risk edit surface in
  the repo — 3,349 lines against 2,107 lines of tests — and nothing before
  Phase 4 needs it.
- **Do not let reading errors into the memory system.**
- **Do not rebuild the engine.** Vendor it. The value is in the tuning and the
  findings, and both are invisible in a rewrite.
- **Do not ever ask a three- or four-year-old to read.** The gate is not a
  preference; it is the product.
- **Do not add a third ask to a five-beat adventure.** The ratio is the
  invisibility.
