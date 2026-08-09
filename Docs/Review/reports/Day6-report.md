# Day 6 Review Report — Illustration Consistency & Latency-Hiding

**Reviewed:** 2026-08-09 · **Commit:** `2f1d099` ("Day 6: illustration consistency + latency-hiding") · **Tree at review:** `2f1d099`, clean (only the pre-existing untracked `Docs/Day6-Build.txt`) · **Configured model:** `AI_BEAT_MODEL=nvidia/nemotron-3-super-120b-a12b:free`, `AI_PROVIDER_ORDER=openrouter,anthropic` · **Reviewer:** Claude Code (review session, no code modified)

## Overall verdict

**FIX-THEN-SHIP** — no P0. The day's headline item, F6, is genuinely fixed, and I verified it against the *real* outage rather than a simulation of it.

Cloudflare is still answering `429 … you have used up your daily free allocation of 10,000 neurons` — the same error that produced the Day-5 founder-test failure. So I drove a real adventure through the real SSE generator with the real dead provider behind it. The child got the narration at **t=11.14s** and their turn at **t=11.61s** — **0.47 seconds later**, with no picture, three labelled choice cards and a spoken question. On Day 5 that same condition was a **120-second dead screen**. The stream then stayed open to t=56.89s still watching for the illustration, so non-essential did not become abandoned. That is criterion 5's real test and it passes.

The consistency machinery is also real, and it works on the *configured model* rather than only on fixtures — which matters, because nothing in the test suite proves a live model actually fills in the fields the whole mechanism reads. I played a full 7-beat adventure: the model populated `characters_present` on all seven beats and `new_characters` on two, an **Elephant** introduced at beat 4 came back in beats 5, 6 and 7 with a **byte-identical descriptor line**, and the companion's canonical descriptor plus the versioned style tokens reached **7 of 7** illustration prompts.

Three things stop this being a SHIP:

1. **The companion yawns at joyful moments.** `canonical_expression("joyful_calm")` returns `sleepy`, because `calm` is a synonym for sleepy and the scan is right-to-left. The beat then asks the illustrator for *"mid-yawn with drooping eyelids, curled slightly."* It fired live in my playthrough (beat 6) and hits 2 of the 29 real beats in the dev database. It is one entry in one list, and it is precisely the uncanny mismatch §8.2 says breaks attachment.
2. **Criterion 4 is not met and the Day-7 handover overstates it.** Across one real adventure the illustration cache was **7 generated / 0 hits — 0%**. Every beat is a unique scene, so every scene render is a miss by construction. Replays are 100% hits and expressions are genuinely pre-warmed, but Day 6 §9's end-state line — *"cache hit-rate is high and per-adventure cost dropped"* — is not true and must not be carried forward. The build notes say this honestly; the acceptance criterion and the handover still don't.
3. **Prefetched media was not ready at a single transition I measured.** `nextBeatReady=false` on every open, and the consequence took **19.78s** from the tap. The motion covers it exactly as designed — so this is not a P0 — but criterion 7's "usually ready on transition" is not demonstrated, and the reason is structural: the client reopens the stream the instant the choice POST returns, so the prefetch has ~0s of head start.

**On real renders:** not possible today — the image provider is out of quota, so every picture I could produce came from the offline placeholder adapter. "Recognisably the same companion" is therefore verified as *identical prompt text and identical cache keys*, not as pixels. **That eyeball check stays with the founder** (criteria 1 and 2, the contact sheet).

---

## Scorecard

| # | Criterion | Verdict | Evidence (cmd/file → result) |
|---|-----------|---------|------------------------------|
| 1 | Companion recognisably the same character in every illustration | 🟡 | **Mechanism verified on live model output.** Full 7-beat adventure on the configured model: `cast.refs_for` put the companion in **7/7** `CHARACTERS` blocks with the identical canonical descriptor, and `[style v1.0.0]` in **7/7** `STYLE` blocks. `story.illustration_cast` logged a cast on every beat (`cast=Ruby`, `cast=Ruby,Elephant`, `cast=Ruby,Elephant,Spider`, …), so the Day-5 defect — the companion alone reaching only beat 1 — is closed at `orchestrator.py:1266-1275`. The harness (`render_consistency --beats 6`) rendered a 6-picture sequence in six deliberately different settings; the companion descriptor was byte-identical in all six, with only the per-picture expression clause varying (5 distinct cues). **Not ✅ because no real image model could be called** — `IMAGE_PROVIDER=cloudflare` returns 429; every file is `local-placeholder` SVG, which is drawn *from* the prompt and so cannot falsify the prompt. See "Unverified items". |
| 2 | Recurring character consistent beat 2 → beat 4, persisted + reference-driven | ✅ | **Verified twice, once on real model output.** *Live:* the model introduced `elephant` at beat 4 and `spider` at beat 5 via `new_characters`; both persisted as `companion.Character` rows through the shipping path (`cast.character_remembered` in the log), both named in beats 4→7, and each produced **exactly 1 distinct descriptor line** across all its appearances. *Harness:* Bramble is written at beat 2 and beat 4's `CHARACTERS` block is byte-identical (`- Bramble: a small round frog, moss-green with a paler belly…`, `cat -A` shows one unique line for two occurrences), with `new_characters == []` at beat 4 — so beat 4 read the row rather than re-inventing the frog. First-description-wins is enforced by `get_or_create` defaults in `cast.remember` (`cast.py:137-156`), which is what makes it hold. Anchor recorded on first render (`cast.record_reference`, anchor `02000768-…`). |
| 3 | Every illustration prompt provably includes style tokens + character references | ✅ | Beat-1 prompt pasted in full below: `SCENE:` / `CHARACTERS:` / `STYLE:`, with the persisted descriptor, the `In this picture:` expression cue, and `[style v1.0.0] soft painterly children's storybook illustration…`. 6/6 harness prompts and 7/7 live prompts carry both. **Negative test as the brief asks:** with the refs removed, `character_signature` collapses to `(no characters)` and the block becomes `- (no named characters; the scene itself is the subject)` — a different prompt and a different cache key. Consistency is engineered, not luck. 🔵 The `REFERENCES:` block is **deliberately absent**: it is emitted only when the selected provider declares `supports_image_reference`, and no implemented provider does (`prompts.py:123-160`). Honest, documented, and the right call for a text-only model — but it means the reference *images* are inert today and consistency rests entirely on the descriptor. Logged P2-1. |
| 4 | High cache hit-rate across an adventure; expressions pre-warmed; provider calls dropped vs Day 5 | 🟡 | **Pre-warm: ✅.** All five canonical expressions reported `pre-warmed`, and `build_assets` is idempotent, so a common emotion costs nothing. **Hit-rate across an adventure: 0%.** Real 7-beat adventure through `illustrate_beat`: `generated=7 hits=0 hit_rate=0%`, all `local-placeholder`. Each beat is a unique scene, so each scene render is a miss whatever the cast hashing does. **Replay: 100%** — `render_consistency` cold = `generated 6 / hits 0`, immediately re-run = `generated 0 / hits 6`, so nothing is ever paid for twice. **"Dropped vs Day 5": not true.** Day 6 holds per-adventure provider calls flat; what it reduces is repeat cost. The build notes state this plainly; the acceptance criterion and Day 6 §9's handover line do not. Per the review brief's own rule ("a low hit-rate is 🟡/🔵 even if visuals look fine") this is 🟡. See P1-2. |
| 5 | **Zero visible spinners or blank flashes**; gaps covered by narration/motion via `SceneTransition` | ✅ | **The real test, run against the real outage.** `IMAGE_PROVIDER=cloudflare` (genuinely 429), voice healthy: `: open` → `beat` @ **11.14s** → `media`(audio) @ 11.56s → `prefetch` + `done{awaiting_choice}` @ **11.61s**. The beat handed over is playable: `scene:{imageUrl:null,status:"pending"}`, narration present, `audioUrl` present, 3 options each with a label, `interaction.type=choose`. The illustration never arrived and the stream kept following it to **t=56.89s**. `ESSENTIAL_STAGES = {voice}` (`streaming.py:110`) is what does it. **The pre-loop handover works too** — a replayed beat with its voice already on the `beat` frame reached `done` at **t=0.12s with zero `media` events**, i.e. it did not wait out an illustration inside a loop that only turns when an asset lands. **Client side:** `grep -rniE "animate-spin\|role=\"progressbar\"\|spinner\|loading\|please wait\|error\|failed" src/routes/child/ src/components/SceneTransition.tsx` returns **comments only** on the story surface; `SceneTransition` holds the outgoing scene (`scene-leaving`) so the picture area is never empty, and the gap carries a line of *story* (`waitingLine`), never a status. 🔵 One pre-existing exception outside this day's scope: `ChildHome.tsx:63` renders `offline` / `waking up` on a child route — P2-2. |
| 6 | `prefers-reduced-motion` disables motion cleanly without breaking flow | ✅ | A real branch, not a decoration. Under `reduce` the outgoing layer is **never mounted** (`SceneTransition.tsx:96`), because its removal waits on an `animationend` that would never fire — the failure mode of getting this wrong is the previous beat's picture stuck over the current one, permanently, for the person who asked for *less* motion. Everything else collapses through `motion-safe:` plus `--motion-fast/base/slow: 0ms` under `@media (prefers-reduced-motion: reduce)` (`design/tokens.css:123-127`). The hook subscribes rather than reading once, and handles Safari's `addListener`. Tests assert the caption still appears, `scene-leaving` is absent, the old scene is gone and the new one is present, and `queryByRole('progressbar')` is null. 🔵 jsdom only — no browser executes the `motion-safe:` variants or the zeroed tokens (P2-3). |
| 7 | Prefetched media usually ready on transition; `make lint && make test` + CI green; no Day-5 regression | 🟡 | **Green, all run by hand at `2f1d099`:** `pytest` → **382 passed**; `vitest` → **87 passed** (6 files); `ruff check` → *All checks passed*; `ruff format --check` → *123 files already formatted*; `mypy` → *no issues in 146 source files*; `eslint` + `prettier --check` clean; `tsc --noEmit` clean; `makemigrations --check --dry-run` → *No changes detected*; `migrate --plan` → *No planned migration operations*. **CI green — verified:** run `31315074510` for `2f1d099` → completed/success, 3m55s. **No Day-5 regression:** my live adventure ran to a warm close (beat 7, `final=1`, `interaction=none`, `choices=0`), choices recorded, no fail state. **Prefetch readiness: not demonstrated.** `nextBeatReady=false` at every open I measured, and the consequence took **19.78s** from the tap (cold first beat: 44.73s). Covered by the path-walk and a story line — so not a P0 — but "usually ready" is not what I saw. See P1-3. |

**Counts:** ✅ 4 · 🟡 3 · ❌ 0 · 🔵 3 (in #3, #5, #6)

---

## Evidence: the F6 outage, reproduced against the real dead provider

Cloudflare, live, at review time:

```
FAIL: MediaProviderError Image provider returned 429 for model '@cf/black-forest-labs/flux-1-schnell':
{"errors":[{"message":"AiError: AiError: you have used up your daily free allocation of 10,000 neurons,
please upgrade to Cloudflare's Workers Paid plan…","code":4006}],"success":false,…}
```

A real adventure streamed through `streaming.stream_beat()` with a real Celery worker behind it, `IMAGE_PROVIDER=cloudflare`, production constants (`MEDIA_TURN_BUDGET_S=20.0`, `MEDIA_FOLLOW_TIMEOUT_S=45.0`, `ESSENTIAL_STAGES=['voice']`):

```
  t=  0.00s  : open
  t= 11.14s  event: beat      {"seq":1,"narration":"Ruby rolls into the sunny garden…","scene":{"imageUrl":null,…
  t= 11.56s  event: media     {"audioUrl":"/media/assets/b9c33a7b7f93d78faa8751becc13983c.wav"}
  t= 11.61s  event: prefetch  {"nextBeatReady":false}
  t= 11.61s  event: done      {"status":"awaiting_choice"}
--- stream closed at t=56.89s (still following the picture that was never coming) ---

illustration url : None
voice url        : '/media/assets/b9c33a7b7f93d78faa8751becc13983c.wav'
options          : [('toward_leaf', True), ('look_around', True), ('hold_hands', True)]
interaction type : choose
```

**0.47s from narration to the child's turn, with no picture.** Day 5's number for the same condition was up to 120s.

### The other half: what happens when the *voice* is the thing that is down

My first run hit a double outage by accident, and it is worth recording because it is live on the founder's own keys right now — ElevenLabs answered:

```
401 … "code":"quota_exceeded","message":"This request exceeds your quota of 10000.
You have 62 credits remaining, while 188 credits are required for this request."
```

With both providers out, the beat frame landed at t=37.61s and `done` at t=58.58s — **20.97s of narration on screen with nothing tappable**, and the log says exactly why:

```
story.media_unfinished beat=… missing=choice:…,illustration,voice blocking=voice budget_s=20
```

That is `MEDIA_TURN_BUDGET_S` behaving precisely as designed, and it is the reason the fix did not simply move the dead screen from the image door to the voice door. It is still a 21-second wait for a four-year-old who cannot read the narration on screen. See P1-4.

---

## Evidence: a real prompt, and the recurring cast on the configured model

Beat 1's illustration prompt, in full, from `render_consistency`:

```
SCENE:
standing at the edge of a mossy riverbank at dawn, mist rising off the water, looking out across the reeds

CHARACTERS:
- Ruby: Ruby, a small, rounded robot the height of a toddler with a large dome-shaped head and stubby
  arms. The body is teal with sea glass blue panels and deep navy blue accents, featuring two large,
  glowing oval eyes. Signature details: a thin antenna with a glowing bead on the end; rounded, smooth
  edges. In this picture: head tilted, leaning forward with wide interested eyes, one hand raised as if
  about to point

STYLE:
[style v1.0.0] soft painterly children's storybook illustration, watercolour and gouache, warm palette,
rounded friendly forms, gentle diffused light, visible paper grain, picture-book composition with
generous negative space, hand-painted edges, cosy and inviting
```

The 7-beat live adventure, which is the part no test covers — the configured model actually filling in the fields the whole mechanism reads:

```
  beat 1: emotion=playful_curious present=['reviewprobe','ruby']                     new=[]           cast=['Ruby']
  beat 2: emotion=playful_curious present=['reviewprobe','ruby']                     new=[]           cast=['Ruby']
  beat 3: emotion=playful_curious present=['reviewprobe','ruby']                     new=[]           cast=['Ruby']
  beat 4: emotion=playful_curious present=['reviewprobe','ruby','spider','elephant'] new=['elephant'] cast=['Ruby','Elephant']
  beat 5: emotion=playful_curious present=['reviewprobe','ruby','elephant','spider'] new=['spider']   cast=['Ruby','Elephant','Spider']
  beat 6: emotion=joyful_calm     present=['reviewprobe','ruby','spider','elephant'] new=[]           cast=['Ruby','Spider','Elephant']
  beat 7: emotion=joyful_warm     present=['ruby','spider','elephant','reviewprobe'] new=[]           cast=['Ruby','Spider','Elephant']

  elephant: named in beats [4,5,6,7] — distinct descriptor lines across those beats: 1
  spider  : named in beats [4,5,6,7] — distinct descriptor lines across those beats: 1
  companion in CHARACTERS + style tokens: 7/7 beats
```

Note beat 4 names `spider` as present *before* any beat introduced it; `cast.refs_for` skips a key it holds no row for, so that picture simply had no spider descriptor. Correct fallback, worth knowing (P2-4).

---

## Evidence: the companion yawns at a joyful moment

`canonical_expression` scans the emotion right-to-left and `calm` is listed as a synonym of `sleepy` (`scenes.py:118-128`), so:

```
  joyful_calm            x1   -> sleepy
  warm_peaceful          x1   -> sleepy
  joyful_warm            x2   -> happy
  playful_curious        x22  -> curious
  playful_joyful         x1   -> happy
  playful_surprised      x1   -> happy
  warm_curiosity         x1   -> curious
```

(every distinct `emotion` on the 29 real beats in the dev database at review time)

The cue that then reaches the illustrator is *"mid-yawn with drooping eyelids, curled slightly."* It fired live at beat 6 of my playthrough — `story.illustration_cast … expression=sleepy` on a beat the model labelled **joyful_calm**. The right-to-left "the head noun is last" heuristic is sound; `calm` and `peaceful` being sleepy synonyms is not, because a calm joy and a sleepy calm are different faces and only one of them is right ~7% of the time.

---

## What's working well

- **F6 was fixed at the right layer.** Making the illustration non-essential is the correct answer, and the three consequences the brief did not name were all found and closed by the builder: the voice becoming the new gate needed its own bound (`MEDIA_TURN_BUDGET_S`), the handover had to be decided *before* the follow loop as well as inside it, and the 120s follow timeout stopped earning its keep once the child was already playing. I verified the pre-loop path independently (done at t=0.12s, zero media events).
- **First-description-wins is the right mechanism** and it is enforced structurally (`get_or_create` defaults + a unique constraint + an `IntegrityError` adopt), not by a convention someone has to remember.
- **Choice icons are deliberately cast-free** (`illustrate_choice`, `orchestrator.py:1285-1315`), so "a mossy stepping stone" caches across every child who is ever offered one. That is the only cross-adventure cost lever in the day and it is pointed the right way.
- **The honesty of the code is unusual and load-bearing.** `REFERENCES:` is emitted only when a provider can consume it; `supports_image_reference` is read from the adapter rather than hard-coded; `render_consistency` prints `descriptor-only` rather than implying an anchor is doing work. A less honest version of this day would have shipped an inert `REFERENCES:` block and looked complete.
- **The build notes refuse a claim they cannot support.** "Cost per adventure dropped" was not manufactured. My measurement agrees with the note, not the criterion.
- **`character_hash` includes the expression but a character without one hashes exactly as before**, so adding it invalidated no cached render — a genuinely careful piece of cache design.

---

## Punch-list (hand this to the next Build session)

### P0 — blockers
*(none)*

### P1 — should fix
1. **P1-1 — The companion is drawn mid-yawn at joyful and peaceful moments.** `EXPRESSION_SYNONYMS["sleepy"]` contains `calm`, `quiet`, `peaceful` and `restful`, so `joyful_calm` → `sleepy` and `warm_peaceful` → `sleepy` (`companion/scenes.py:118-128`). Observed live at beat 6 of a real playthrough; 2 of 29 real beats. Either drop `calm`/`peaceful`/`quiet`/`restful` (an emotion that matches nothing correctly gets *no* expression clause, which the module already documents as the safe outcome), or require sleepy to be matched by an explicitly drowsy word. One-line fix; the effect is a companion whose face contradicts the scene, which is exactly §8.2's uncanny.
2. **P1-2 — Criterion 4's cost claim is false and Day 6 §9 would carry it into Day 7.** Measured 0% illustration cache hit-rate across one real adventure (7 generated, 0 hits); per-adventure provider calls are flat vs Day 5, not down. Restate the end-state as *"repeat cost is eliminated (replays are 100% hits) and expressions are pre-warmed; per-adventure cost is unchanged"*, or land a real lever. Leaving §9 as written means Day 7 inherits a unit-economics claim nothing supports (§22.4).
3. **P1-3 — The prefetch has no head start, so it is never ready at the transition.** `nextBeatReady=false` at every open I measured; the consequence took 19.78s from tap. The client POSTs the choice and reopens the stream immediately (`StoryPage.tsx:286-303`), so the worker the POST started has ~0s before the reconnect arrives. Either accept it and restate criterion 7 (the motion *does* cover the gap — that part is verified), or give the prefetch somewhere to land, e.g. keep serving the current beat until the consequence is written rather than reopening straight away.
4. **P1-4 — A TTS outage is still a ~21s dead screen, and it is live right now.** Measured 20.97s from narration to turn with `blocking=voice budget_s=20`. This is the designed bound working, not a bug — but ElevenLabs is currently returning `quota_exceeded` (62 credits remaining, 188 required), so today every beat on the founder's own keys costs a four-year-old 21 seconds of untappable screen. Two things: top up or fail over the voice, and consider whether 20s is the right number when the fallback (narration on screen for a pre-reader) is not something the child can use.

### P2 — improvements (non-blocking, log for later)
1. **P2-1 — Character reference images are inert.** No implemented provider declares `supports_image_reference`, so the anchor URLs recorded by `cast.record_reference` reach nothing and §8.2's reference set is descriptor-only. Correctly gated and clearly documented — but the *reference-driven* half of criteria 1–2 is text, not images, and that should be explicit wherever the mechanism is described.
2. **P2-2 — A child route still renders `offline` / `waking up`.** `ChildHome.tsx:63` puts a health status on a screen a three-year-old sees. Pre-existing (Day 1), not a Day-6 regression, but it is the one place the no-status rule is broken.
3. **P2-3 — Motion is verified in jsdom only.** No browser executes the `motion-safe:` variants, the `page-turn`/`page-leave` keyframes, or the zeroed `--motion-*` tokens. The reduced-motion *branch* is well tested; the CSS it depends on is not. A single Playwright run at both preference settings would close it.
4. **P2-4 — A beat can name a character one beat before it introduces it.** Beat 4 listed `spider` in `characters_present` while `new_characters` carried it only at beat 5. `cast.refs_for` correctly skips a key with no row, so that picture drew a spider from the scene prose alone. Worth a log line at minimum, so an operator can see how often the model runs ahead of itself.
5. **P2-5 — `render_consistency` persists its character with `first_seen_adventure_id=None`** (`cast.remember` is called without an `adventure_id`), so the harness's Bramble reports `first seen: None`. Cosmetic, in a dev tool.

---

## Scope & risk notes

- **Scope discipline held.** Nothing from Day 7+ appears: no memory retrieval, no safety runtime, no learning engine, no new gameplay. `seed_memories` still says real retrieval is Days 8-9; the forced count beat is still the Day-5 stand-in; the stream still ends silently on an unrecoverable generation with the Day-7 fallback marked as future work.
- **No Day-5 regression.** 382 backend tests including the whole Day-5 suite pass, and my live adventure ran to a warm close with `interaction=none`, `choices=0`, no fail state, and choices recorded with `latency_ms`/`retries`/`assisted`.
- **The biggest remaining risk on this day is unmeasurable from here.** Every consistency claim is a claim about prompt text. Whether FLUX actually draws the same robot twice from the same descriptor is the one question the code cannot answer, and today the provider cannot be called at all.
- **`story.choice_options_alike` fired once** during my run (`['Look left','Look right','Look ahead']`) — the Day-5 near-duplicate detector working as designed (logged, still offered). Not a Day-6 matter.

## Unverified items (and why)

- **Real renders — criteria 1 and 2's pixels.** `IMAGE_PROVIDER=cloudflare` returns `429 daily allocation exhausted`, so no real image model could be called during this review. Every picture produced is `local-placeholder` SVG, which is *drawn from the prompt* and therefore cannot independently confirm what a real model does with it. **This remains the founder's contact-sheet check:** restore image quota, run `make render-consistency CHILD=<uuid>`, open the six files in order, and answer the two questions no assertion can — is that the same companion six times, and is the frog in beat 2 the frog in beat 4.
- **The seam, watched by a human.** No browser was driven. The page-turn, the path-walk and the reduced-motion collapse are verified by type and by jsdom assertions, never by watching one.
- **Cache behaviour under a real image provider.** The 0% within-adventure hit-rate is a property of the cache key (unique scene text per beat) and holds regardless of provider, but the absolute numbers were measured against the placeholder adapter.
- **Fleet-wide cost.** `media_service.STATS` is per-process by design. Real per-adventure cost instrumentation is §22.4 and a later day.

### Note on side effects of this review

No repository file other than this report was created or modified; `git status` shows the tree at `2f1d099` clean apart from the pre-existing untracked `Docs/Day6-Build.txt`.

One throwaway probe child (`ReviewProbe`, cloned from an existing child so it had a ready companion) was created in the dev database, used for two adventures and two harness runs, and **deleted** along with its companion, adventures, beats, `Character` rows and media. The founder's data is unchanged and was re-counted after cleanup: **3 children, 130 media rows, 0 characters** — identical to the state at the start of the review. A Celery worker was started and stopped three times against the dev broker.

**Provider spend:** one Cloudflare image call (rejected, 429 — no neurons consumed), one ElevenLabs call (rejected, quota_exceeded), and roughly a dozen beat/plan generations on the free OpenRouter model. All image and voice assets produced during the review came from the local placeholder adapters.
