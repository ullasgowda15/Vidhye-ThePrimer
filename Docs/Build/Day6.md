# Build — Day 6: Illustration Consistency & Latency-Hiding

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 7.
>
> **Source of truth:** `docs/PRODUCT.md` — §8.2 (character consistency), §5.8/§16.6 (illustration pipeline), §4.9 (latency‑hiding), §14.7 (motion), §15.8 (motion tokens), §9.6 (canonical expressions).
>
> **If handed `docs/Review/reports/Day6-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Full branching story loop works end to end (2–3 illustrated choices, specific remembered consequences, bounded continuity, no fail states, warm close, prefetch, no spinner). **M1 complete.** Visuals still generic‑ish; no memory, safety hardening, or learning engine.

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
4. Cache hit‑rate across an adventure is high (report hit/miss counts); companion expressions are pre‑warmed; provider calls per adventure dropped vs. Day 5.
5. **Zero visible spinners or blank flashes**; all gaps are covered by narration/motion via `SceneTransition`.
6. `prefers-reduced-motion` disables motion cleanly without breaking flow.
7. Prefetched media is usually ready on transition (report typical wait); `make lint && make test` + **CI green**; no regression to Day 5's loop.

## 8. Founder manual test
Play a full adventure and watch the companion + any recurring character — the *same* being throughout. Watch the seams between beats — a smooth, magical page‑turn, never a loading screen. Toggle reduced‑motion and confirm it still flows. Check the logs: most images should be cache hits.

## 9. End state to hand to Day 7
Illustrations are style‑ and character‑consistent across a full adventure (companion + recurring cast), driven by reference injection + persisted character entities + caching; all latency is masked by narration/motion via `SceneTransition`, with reduced‑motion support; cache hit‑rate is high and per‑adventure cost dropped. The story now *feels* like one magical world. Still no memory, safety hardening, or learning engine.
