# Build — Day 6 FIX: expression, honest cost, prefetch, voice failover

> **Session type:** BUILD (fix pass for Day 6). Read fully, fix everything in scope, run the **Self-review** in §6, then **Commit & push** in §7. Do **not** start Day 7.
>
> **Inputs:** this file · `docs/Build/Day6.md` · `docs/Review/reports/Day6-report.md` (file/line hints below are from it). **Source of truth:** PRODUCT.md §8.2 (consistency), §4.9 (latency-hiding), §22.4 (cost).
>
> **Why:** Day 6 reviewed **FIX-THEN-SHIP, no P0**. F6 is fixed. Four P1s remain — one uncanny-face bug, one false cost claim that would poison Day 7, one over-stated prefetch criterion, and a voice-outage dead screen that is **live on the founder's keys right now**.

## 1. P1-1 — the companion yawns at joyful/calm moments (one line)
`EXPRESSION_SYNONYMS["sleepy"]` contains `calm`, `quiet`, `peaceful`, `restful` (`companion/scenes.py:118-128`), so `joyful_calm` → `sleepy` and the illustrator is asked for *"mid-yawn, drooping eyelids."* Fired live (beat 6), hits 2/29 real beats. **Fix:** remove `calm`/`quiet`/`peaceful`/`restful` from the sleepy set (an emotion matching nothing correctly gets **no** expression clause — the module already documents that as the safe outcome), or require `sleepy` to be matched only by an explicitly drowsy word. Add a test: `joyful_calm` and `warm_peaceful` do **not** resolve to `sleepy`.

## 2. P1-2 — stop carrying a false cost claim into Day 7 (docs honesty)
Measured 0% within-adventure illustration cache hit-rate (7 generated / 0 hits); per-adventure provider calls are **flat** vs Day 5, not down. **Fix:** restate criterion 4 and `docs/Build/Day6.md` §9's end-state to the truth — *"repeat cost is eliminated (replays 100% hits) and expressions are pre-warmed; per-adventure image cost is unchanged"* — so Day 7 does not inherit an unsupported §22.4 claim. No code lever required; this is an honesty fix.

## 3. P1-3 — the prefetch has no head start
`nextBeatReady=false` at every open; consequence took ~20s from tap because the client POSTs the choice and reopens the stream immediately (`StoryPage.tsx:286-303`), giving the worker ~0s. **Fix (prefer the first):**
- (a) give the prefetch somewhere to land — keep serving/holding the **current** beat until the consequence is written, rather than reopening the stream instantly; **or**
- (b) if (a) is non-trivial, **restate criterion 7 honestly**: the motion *does* cover the gap (verified), so drop "usually ready on transition" rather than claim what isn't shown. Say which you chose in the self-review.

## 4. P1-4 — a voice outage is still a dead screen for a pre-reader
`MEDIA_TURN_BUDGET_S=20` bounds the wait, but when voice is truly down the child gets ~21s of on-screen narration they **cannot read** and nothing to tap. It's live now: ElevenLabs returns `quota_exceeded` (62/188 credits). **Fix:**
- Add a **TTS fallback**: the voice adapter should fail over to a second provider when the primary errors/quota-exceeds (Google Cloud TTS is the swappable free option), the same shape as the model router — so a single provider outage doesn't leave a pre-reader with a silent, unreadable screen. Keep `MEDIA_TURN_BUDGET_S` as the final backstop.
- Do **not** widen the budget as the "fix" — a longer wait on unreadable text helps no one.

## 5. Do NOT do
- ❌ No Day-7 work (no safety runtime, no safe-fallback-beat — that's Day 7). ❌ Don't add memory/learning. ❌ Don't touch `.env`/`backend/media/`. ❌ Don't weaken the F6 voice-gate or `ESSENTIAL_STAGES={voice}`.

## 6. Self-review & evaluation (REQUIRED before you stop)
Write a short evaluation rating each **Verified / Partial / Unverified** with evidence:
1. `make lint && make test` green; any migration clean.
2. `joyful_calm`/`warm_peaceful` no longer draw a sleepy/yawning companion (test + a real beat's expression cue).
3. Criterion 4 / §9 restated to the measured truth.
4. Prefetch: either a real head start (report a `nextBeatReady=true` transition) or criterion 7 restated — state which.
5. Voice failover: kill/quota the primary TTS and confirm the beat still gets a voice from the fallback (or, if you can't reach a second provider, an honest note + the failover code path unit-tested).
6. What you could not verify.

## 7. Commit & push
`git status` (no secrets/media) → stage fix + migration + `docs/Build/Day6-fix.md` → commit `Day 6 fix: expression synonyms, honest cost, prefetch, TTS failover` → `git push`, confirm Actions green.

## 8. End state
The companion's face matches the scene, the cost claim is honest, prefetch is either given a head start or described truthfully, and a single voice-provider outage no longer strands a pre-reader. Ready for Day 7.

---
**Founder note (do this regardless of the build):** both your free providers are currently exhausted — Cloudflare image (`429 daily allocation`, resets daily) and ElevenLabs (`quota_exceeded`, 62/188 credits). To test real media now: wait for the Cloudflare daily reset, and either top up ElevenLabs or set the TTS adapter to **Google Cloud TTS** (1M free chars/mo). Until then live media testing (and the real-render contact-sheet check for criteria 1–2) is blocked.
