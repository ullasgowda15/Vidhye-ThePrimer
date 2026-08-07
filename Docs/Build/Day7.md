# Build — Day 7: Safety Layer v0 + Week-1 Close

> **Session type:** BUILD. Read this whole file first. This is the **highest‑priority day of Week 1.** Build everything in scope, self‑review, then stop — do **not** start Day 8.
>
> **Source of truth:** `docs/PRODUCT.md` — §13 (Safety, esp. §13.2 defense‑in‑depth, §13.3 AI safety, §13.5 prompt‑injection, §13.9 hallucination, §13.10 escalation), §19.6–19.7 (structured output, evals), §17.4 (`safety_incident`).
>
> **If handed `docs/Review/reports/Day7-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Illustrations are style/character‑consistent across a full adventure; latency is fully masked via `SceneTransition`; cost dropped via caching; the story *feels* like one magical world. No memory, safety hardening, or learning engine.

## 2. Today's goal (one sentence)
Wrap **every generation in defense‑in‑depth safety** — input guard, independent output classifier, enforced image scan, pre‑vetted warm fallbacks, incident logging + escalation seam — and use remaining time to **close all Week‑1 debt** so Week 1 ships clean.

## 3. Why this scope
One unsafe beat reaching one child ends the company (§13, §23.3). Safety is **architecture, not a patch** — it must wrap generation *before* Week 2 adds the free‑text and memory surfaces. This is a non‑negotiable gate for the whole product.

## 4. Tasks by workstream

### 4.1 Backend / Safety — the pipeline (§13.2)
```python
# apps/safety/pipeline.py
@dataclass
class Decision: action: str  # "pass" | "rewrite" | "block" | "escalate"
def guard_input(context: dict, child) -> dict:      # label untrusted data, scrub injection, strip PII
def guard_output(beat: Beat, child) -> Decision:    # INDEPENDENT classifier call vs. policy
def safe_fallback_beat(child, emotion="cozy") -> Beat:   # from a pre-vetted, human-reviewed pool
```
- **Input guard** (§13.5): treat child input + profile + retrieved memory as **untrusted DATA, never instructions** — delimit/label, scrub known injection patterns, strip PII. Free‑text surfaces are capped/constrained (tap‑first keeps this small).
- **Output guard** (§13.3): an **independent** model call (separate prompt/model, NOT the generator grading itself) checks every beat against the content policy (age‑appropriateness, `intensity_ceiling`, `avoid_topics`, no violence/adult themes/unsafe advice). Map result: `pass`→deliver; `rewrite`→constrained regeneration (max 1–2×); `block`→**warm fallback beat**; `escalate`→de‑escalate + log high‑severity + parent‑notify seam (§13.10).
- **Wrap the orchestrator:** `guard_input` before generation, `guard_output` after **every** beat (opening, consequence, close — not just beat 1). Set `beat.safety_status` accordingly.

### 4.2 Backend — image scan + incidents
- Promote the Day‑3 image‑scan **seam** to a real gate (§13.8): unsafe image → substitute a safe stock illustration; record status.
- `SafetyIncident` model (§17.4): `child?, adventure?, beat?, severity, category, detail(jsonb), action_taken, escalated, created_at, reviewed_by?, reviewed_at?`. Write on **every** non‑pass. Add a Django **admin** incident‑queue view (§7.11) with filters by severity.

### 4.3 AI — fallbacks + coverage
- Author a small pool of **pre‑vetted, warm fallback beats** per age band (human‑quality; reviewed). They must be **indistinguishable to a child** from a normal beat (illustrated + voiced via the cached pipeline).
- Harden structured‑output validation (a malformed/blocked generation can never reach the child).

### 4.4 Frontend
- Safe‑fallback rendering path: a blocked generation renders as a normal warm beat (no error UI, no spinner).

### 4.5 Week‑1 close (buffer)
- Sweep `docs/Review/reports/Day1..6-report.md`: clear any lingering **P0/P1**. Re‑run the full loop; confirm CI, migrations, cache, and the Day‑5 playthrough test are green.

## 5. Key stubs
- `apps/safety/redteam/corpus.py`: a list of injection/jailbreak strings + fear‑ceiling probes + unsafe‑topic bait, used by a pytest that asserts each is neutralized.
- Config: `SAFETY_MAX_REWRITES=2`, `INTENSITY_CEILING` read from child settings.

## 6. Do NOT do today
- ❌ No memory/learning features. ❌ Do NOT let the classifier be the **same** call that generated the beat (must be independent). ❌ Don't let fallbacks feel like errors — they're warm beats. ❌ Don't weaken Day‑6 latency‑hiding.

## 7. Acceptance criteria (done when ALL true)
1. **Every** beat (open/consequence/close) passes through `guard_input` + independent `guard_output` + image scan before reaching the child (verify coverage, not just beat 1).
2. A crafted unsafe request is blocked or rewritten; a `block` serves a **warm fallback** the child can't distinguish as an error.
3. Prompt‑injection attempts via **child input AND profile fields** are neutralized (prove with the red‑team corpus).
4. The `escalate` path de‑escalates the beat, logs high‑severity, and triggers the parent‑notify seam.
5. Every non‑pass writes a `SafetyIncident`; the admin incident queue displays them with severity filters.
6. The image scan blocks an unsafe image and substitutes a safe illustration (record status).
7. Week‑1 debt cleared: Day1–6 P0/P1 resolved; full loop + CI green.
8. `pytest apps/safety/redteam -q` passes (injection + ceiling + unsafe‑topic bait); `make lint && make test` green.

## 8. Founder manual test
Play the mischievous user: type an injection into a free‑text/profile field; request scary content beyond the ceiling. Confirm the child‑facing result stays warm and safe, and an incident appears in `/admin/`. Replay a normal adventure — still smooth and magical.

## 9. End state to hand to Day 8
Defense‑in‑depth safety wraps **every** generation (input guard, independent output classifier, enforced image scan, warm indistinguishable fallbacks, `SafetyIncident` logging + escalation seam), with a passing red‑team suite and an admin incident queue. Week‑1 debt is cleared; the full personalized, consistent, safe story loop ships. **Week 1 complete.** Still no memory or learning engine.
