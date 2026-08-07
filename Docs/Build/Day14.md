# Build — Day 14: Analytics, Cost Instrumentation, Accessibility & Polish

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 15.
>
> **Source of truth:** `docs/PRODUCT.md` — §22 (Metrics, esp. §22.1 North Star, §22.3 definitions, §22.4 unit economics), §5.6 (analytics pipeline), §19.9 (cost architecture), §14.6 (accessibility), §17.4 (`event`).
>
> **If handed `docs/Review/reports/Day14-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Enforceable parental controls, warm wellbeing behavior with zero child‑directed pull mechanics, working accessibility toggles, and a privacy center (export + delete). Parents in control; child protected. No billing; no analytics/eval hardening.

## 2. Today's goal (one sentence)
Make the product **measurable, affordable, accessible, and polished**: an event pipeline computing activation/retention/completion, per‑generation cost tracing (cost/session), a full accessibility pass, and first‑run/onboarding polish.

## 3. Why this scope
We must measure whether the loop works (§22) and keep unit economics visible from day one (§22.4) — every session costs inference. Accessibility makes the core better for everyone (§14.6). Polish turns "works" into "magical" before real families arrive Day 15.

## 4. Tasks by workstream

### 4.1 Backend — event pipeline + metrics (§5.6, §22.3)
```python
# apps/analytics/events.py
def track(name, *, child=None, parent=None, session=None, **props): ...   # append-only Event rows
```
- Emit events across services (onboarding steps, adventure begin/complete, choice, session end, safety incident). Drain `Event` to a queryable store/warehouse export.
- Compute: **activation** (onboard + first‑story complete), **day‑2 return**, **W1 retention**, **completion rate**, **adventures/child/week** (= the North Star DWA input, §22.1). Keep **product analytics separate** from the parent‑facing curated subset (§5.6) — a hard boundary.

### 4.2 Backend / AI — cost instrumentation (§19.9, §22.4)
- Trace **LLM + image + TTS** cost per generation (LLM tracing, e.g. Langfuse/Helicone spans). Compute **cost/session** and surface it on an internal dashboard/admin view. Attach cost to each generation span so unit economics are always visible.
- Light prompt tuning from the week's real outputs (quality + token reduction).

### 4.3 Frontend — accessibility pass (§14.6) + polish
- Captions for all narration; adjustable audio; reduced‑motion; high‑contrast; dyslexia font; large targets; **WCAG AA** on the parent UI (labels, focus rings, contrast, keyboard nav).
- Polish: onboarding + first‑run flow, empty states, transitions, copy — remove rough edges across child + parent surfaces.

## 5. Key stubs
- Internal metrics view: `/admin/metrics` or a simple protected dashboard reading the warehouse/export.
- `cost_per_session = sum(generation_costs) / sessions` over a window.

## 6. Do NOT do today
- ❌ No billing/paywall (not MVP‑core). ❌ **Do NOT let raw child behavioral data leak into the parent view** (§5.6). ❌ Do NOT add features — this is measure + polish only.

## 7. Acceptance criteria (done when ALL true)
1. Events flow from all services; activation / day‑2 / W1‑retention / completion / adventures‑per‑week are computable — **show the numbers** for seeded data.
2. Per‑generation cost is traced (LLM + image + TTS); **cost/session** is visible on an internal dashboard.
3. Product analytics and the parent‑facing curated subset are **separated** (raw child behavior never appears in the parent view).
4. Accessibility: captions, reduced‑motion, high‑contrast, dyslexia font, adjustable audio all work; the parent UI meets WCAG AA basics (keyboard + contrast + focus).
5. Onboarding/first‑run is polished (no rough empty states or jarring transitions).
6. `make lint && make test` + **CI green**.

## 8. Founder manual test
Play a few sessions, then open the internal dashboard: see activation/retention/completion and **cost/session**. Toggle every accessibility option and confirm it works and persists. Run onboarding fresh and confirm it feels smooth and magical.

## 9. End state to hand to Day 15
An event pipeline with the core metrics (activation/retention/completion/adventures‑per‑week) and per‑generation **cost/session** instrumentation, product vs. parent analytics separated, a completed accessibility pass (WCAG AA basics), and polished onboarding/first‑run. Measurable, affordable, accessible, polished. No billing; eval harness + launch hardening remain.
