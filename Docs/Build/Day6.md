# Build — Day 6: Illustration Consistency & Latency-Hiding

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 7.
>
> **Source of truth:** `docs/PRODUCT.md` — §8.2 (character consistency), §5.8/§16.6 (illustration pipeline), §4.9 (latency‑hiding), §14.7 (motion), §15.8 (motion tokens), §9.6 (canonical expressions).
>
> **If handed `docs/Review/reports/Day6-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Full branching story loop works end to end (2–3 illustrated choices, specific remembered consequences, bounded continuity, no fail states, warm close, prefetch, no spinner). **M1 complete.** Visuals still generic‑ish; no memory, safety hardening, or learning engine.

> **Carried over from the Day 5 founder test (F6) — in scope today.** Latency-hiding has a real hole this day owns: when the image provider is **unavailable** (not merely slow), the child gets narration + voice but **nothing to tap for up to `MEDIA_FOLLOW_TIMEOUT_S = 120s`**, because `done`/`awaiting_choice` is gated on the illustration being an *essential* stage. This was hit for real — Cloudflare returned `429 daily allocation exhausted` — and a 2-minute dead screen flatly violates §4.9. **Fix here:** make the illustration **non-essential to answering** — gate the child's turn on **voice + narration**, not the picture (the question is spoken and the choice cards carry labels the companion reads aloud), so a missing/slow image yields a picture-*less but playable* beat instead of a dead screen. Keep a short budget for the image and let it arrive late or not at all. Note the exposure grows if `AI_BEAT_MODEL` returns 3 choices (4 images/beat), so don't let per-beat image count block the turn. This is the highest-priority item of the day — it's a child-facing safety/UX failure, not a polish nicety.

## 2. Today's goal (one sentence)
Make the story **look and feel like one coherent world**: the companion + any recurring characters are visually consistent across every beat, and all generation latency is fully masked by narration/motion so the child never perceives a wait.

## 3. Why this scope
Inconsistent characters are uncanny and break attachment instantly for a young child (§8.2) — the biggest visual‑quality risk (§23.1). Latency‑hiding is a core UX mechanic, not a nicety (§4.9). Both are quality gates before we layer safety/memory on top.

## 4. Tasks by workstream

### 4.1 AI / Media — reference injection everywhere
- Every illustration prompt = `build_illustration_prompt(scene, character_refs)` (from Day 3): scene + global style tokens + each present character's `visual_descriptor` + reference handle (§8.2). Ensure the **orchestrator passes the present characters** (companion + any cast) into this on every beat, not just beat 1.
- Where the beat's `emotion` matches a companion **canonical expression** (happy/curious/proud/gentle‑concern/sleepy), use that expression asset as an anchor/reference (§9.6).
- **Recurring characters:** when a character appears, load its stored `character` entity + reference set; if newly introduced, **persist** it now (name, descriptor, reference) so it's consistent on its *next* appearance.

### 4.2 AI / Media — cache tuning
- Verify `(style_hash, character_hash, scene_hash)` cache hits across a real adventure; report hit/miss counts. **Pre‑warm** companion expression assets so common emotions never re‑generate.
- Confirm cost per adventure drops materially vs. Day 5 (attach before/after provider‑call counts).

### 4.3 Frontend — motion & latency‑hiding
- `src/components/SceneTransition.tsx`: reusable transition (page‑turn / path‑walk / companion‑"thinking") using motion tokens from §15.8 (`--motion-fast/base/slow`). It **covers the gap** while the next beat + media render.
- Progressive reveal polish: narration + audio start immediately; illustration fades in when ready; **never a blank flash or spinner**.
- Respect `prefers-reduced-motion`: transitions collapse to instant, still no spinner.
- Ensure the Day‑5 prefetch actually has next‑beat media ready on transition in the common case.

## 5. Key stubs
- Dev harness: `python manage.py render_consistency <child_id> --beats 6` renders a full sequence for eyeballing.
- A small provider‑call counter (middleware or adapter‑level) to prove cache effectiveness in tests/logs.

## 6. Do NOT do today
- ❌ No new story/gameplay features. ❌ No memory/learning/safety‑runtime. ❌ Do NOT regress the §8.5 no‑fail / warm‑close behavior while refactoring rendering. ❌ Don't introduce a spinner "just for slow cases" — cover with motion instead.

## 7. Acceptance criteria (done when ALL true)
1. Across a full adventure, the companion is **recognizably the same character** in every illustration (attach the sequence).
2. A recurring character introduced in beat 2 looks consistent when it reappears in beat 4 (persisted + reference‑driven).
3. Every illustration prompt provably includes style tokens + character references (paste a logged prompt).
4. ~~Cache hit‑rate across an adventure is high (report hit/miss counts); companion expressions are pre‑warmed; provider calls per adventure dropped vs. Day 5.~~ **Restated after measurement (Day-6 review, P1-2):** repeat cost is eliminated — a replay of an adventure is **100% hits** (`render_consistency` cold = 6 generated / 0 hits, re-run = 0 generated / 6 hits) — and companion expressions are pre‑warmed, so a common emotion costs nothing. **Per‑adventure image cost is unchanged vs. Day 5:** every beat is a unique scene, so every first render of it is a miss by construction, and one real 7-beat adventure measured `generated=7 hits=0` (0% within-adventure). The only cross-adventure lever this day adds is cast-free choice icons (`illustrate_choice`), which cache across children. **Report hit/miss counts; do not claim a per‑adventure drop.** Real per-adventure cost instrumentation is §22.4 and a later day.
5. **Zero visible spinners or blank flashes**; all gaps are covered by narration/motion via `SceneTransition`.
6. `prefers-reduced-motion` disables motion cleanly without breaking flow.
7. ~~Prefetched media is usually ready on transition (report typical wait)~~ **Restated after measurement (Day-6 review, P1-3):** the gap between a tap and the consequence is **fully covered by motion** — `SceneTransition`'s path-walk plus a line of story, with the outgoing scene held so there is no blank flash — and the child is never shown a wait. The prefetch is **not** ready at a `choose` transition and structurally cannot be: the next beat is unknowable until the child has chosen (`orchestrator.schedule_advance` refuses to guess a branch, because guessing means paying for beats no child will see), so `nextBeatReady` is `false` on every open after a choice and the measured consequence latency was **~20s, covered**. What the prefetch does buy is the beat *after* a `none`-interaction transition and any replay, both of which are hits. **Report the typical wait and that motion covers it; do not claim "usually ready".** `make lint && make test` + **CI green**; no regression to Day 5's loop.

## 8. Founder manual test
Play a full adventure and watch the companion + any recurring character — the *same* being throughout. Watch the seams between beats — a smooth, magical page‑turn, never a loading screen. Toggle reduced‑motion and confirm it still flows. Check the logs: most images should be cache hits.

## 9. End state to hand to Day 7
Illustrations are style‑ and character‑consistent across a full adventure (companion + recurring cast), driven by reference injection + persisted character entities + caching; all latency is masked by narration/motion via `SceneTransition`, with reduced‑motion support. **Repeat cost is eliminated (a replay is 100% cache hits) and companion expressions are pre‑warmed; per‑adventure image cost is unchanged** — each beat is a new scene, so its first render is always a miss. The story now *feels* like one magical world. Still no memory, safety hardening, or learning engine.

> **Do not inherit a unit-economics claim from this day (§22.4).** The original end-state line read *"cache hit‑rate is high and per‑adventure cost dropped"*; it was measured at **0% within-adventure hit-rate** (7 generated / 0 hits on a real 7-beat adventure) and per-adventure provider calls are **flat** vs Day 5. What Day 6 reduced is *repeat* cost, not the cost of playing a story once. Fixed in the Day-6 fix pass (P1-2).
>
> **Also carried forward:** a voice-provider outage no longer strands a pre-reader — the TTS adapter fails over (`TTS_FALLBACK_PROVIDERS`, default `google`) and `MEDIA_TURN_BUDGET_S` remains the final backstop. Day 7's safe-fallback-beat work sits *behind* that, not instead of it.
