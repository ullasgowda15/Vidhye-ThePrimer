# Build — Day 9 FIX: close the review punch-list

> **Session type:** BUILD (fix pass). Read this whole file first. Day 9 was reviewed **FIX-THEN-SHIP — no P0**. Fix the two P1 items below, then re-verify. Do **not** start Day 10.
>
> **Source of truth:** `docs/PRODUCT.md` — §13.3 (text a child sees/hears must pass output guard), §12.6 (parent transparency log), §4.5 (day-two payoff), §8.7 (memory injection). Report reviewed: `docs/Review/reports/Day9-report.md`.

---

## Golden rule for this pass
**Fix only the two P1 issues. Do not touch anything the review marked green.** Cross-child isolation, the token budget, injection-through-memory, the new-child fallback, recall bookkeeping, retrieval ranking — all verified working. Do not refactor, re-tune, or "improve" them. Every change below is additive: a check plus a fallback plus a test. If a change would alter behaviour on the passing paths (correct returning greeting, correct first-meeting), you've gone too far.

---

## P1-1 — The greeting must verify it references a *real* memory
**Problem:** `greeting.build` sets `kind=RETURNING` purely from `bundle.remembered` (child has stored rows), then trusts whatever the model returns. Two bad outputs ship today:
- `remembered=""` → renders "Welcome back!" with nothing under it (the case the module's own docstring calls *worse* than first-meeting).
- `remembered="<a memory that isn't in the table>"` → an invented sentence renders on the child's screen as what the companion remembers.

**Fix (in `_generate`, after `guard_output`):** require `remembered` to be **non-empty AND traceable to a bundle item** — exact match against a `bundle.remembered` item, or a gist match via the existing `salience.is_the_same_memory` helper. On failure, return `None` so the caller degrades to the first-meeting hello (the existing, free fallback path). Do **not** invent a new fallback — reuse the one that already works.

**Tests:** add two, one per bad output above, asserting each degrades to `kind=first_meeting`. Do not weaken the existing passing returning-greeting tests.

## P1-2 — `remembered` must pass the output guard before reaching the child
**Problem:** `_generate` grades only the greeting text; `remembered` is returned unguarded, serialized, and rendered at `ReturningGreeting.tsx:148-155` on the child surface `/welcome/:childId`. §13.3 covers **all** text a child sees, not just the narration.

**Fix — pick one, minimal:**
- (a) Grade both fields together: `_as_gradeable(f"{text}\n{remembered}")`, so an unsafe `remembered` triggers the existing fallback; **or**
- (b) Drop `remembered` from the child scene entirely and keep it only for the parent transparency log (§12.6).

Prefer (a) unless the founder wants the caption gone. Either way it routes through the **existing** `guard_output` path — do not add a second classifier.

**Test:** one case asserting an unsafe `remembered` triggers the fallback (or is absent from the child payload under option b).

---

## Bundled quick win (do only if it costs nothing — else leave for later)
- **P2-4 — tighten the criterion-2 test.** `test_it_greets_by_name_and_references_a_seeded_memory` passes on `"...Sparkle..." in text`, which `ScriptedAIProvider.greeting` always appends — so it can pass without a memory reaching the model. Drop the `or` / assert on `result.remembered` so the test named for criterion 2 actually enforces criterion 2. Same weak `or` in `test_two_children_get_different_correct_greetings` (fix only the positive assertion; its anti-bleed assertions are correct — leave them). This is one assertion change and directly reinforces P1-1's guarantee.

**Everything else (P2-1, P2-2, P2-3, P2-5, P2-6) is explicitly out of scope for this pass** — log for Day 10 planning, do not touch.

---

## Re-verify before you stop (done when ALL true)
1. Both P1 fixes in, each with its new test; the two new tests fail without the fix, pass with it.
2. The **correct** returning greeting (real memory present) still ships `kind=returning` unchanged — no regression on Rina/Omar.
3. The new-child path still ships `kind=first_meeting` with `greeting_calls=0` — no regression.
4. `make lint && make test` green (backend + frontend), `mypy` clean, no new pending migrations.
5. CI green. Commit: `Day 9 fix: greeting verifies a real memory + guards the remembered caption`.

## Do NOT do
- ❌ Do not modify retrieval, ranking, dedupe, the token budget, or cross-child filtering — all verified.
- ❌ Do not start P2-1/2/3/5/6 or any Day 10 work.
- ❌ Do not add a new guard/classifier — reuse `guard_output`.
- ❌ Do not change the first-meeting fallback text or audio path.
