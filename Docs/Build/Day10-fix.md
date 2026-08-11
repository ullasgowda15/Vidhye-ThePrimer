# Build — Day 10 FIX: close the review punch-list

> **Session type:** BUILD (fix pass). Read this whole file first. Day 10 was reviewed **FIX-THEN-SHIP — no P0 outstanding** (the CI stale-schema P0 was already fixed in `3446b0c`, CI green). Fix the items below in priority order, then re-verify. Do **not** start Day 11.
>
> **Source of truth:** `docs/PRODUCT.md` — §10.4 (difficulty), §10.5 (evidence/mastery), §8.10 (learning tag), §10.2 (interpretable model). Report reviewed: `docs/Review/reports/Day10-report.md`.

---

## Golden rule for this pass
**Fix only the issues named below. Do not touch anything the review marked green.** The catalog/seed, selector (ZPD/spacing/interleaving/prerequisite gate), the engine-stamped tag, within-session adaptation, idempotency-in-the-happy-path, no-quiz structure, and scope discipline are all verified working — leave them exactly as they are. Every change here is additive: a clamp, a warn-only probe, a docstring, a deleted dead assertion. If a change would alter the correct returning behaviour (a clean win at the *bottom* of the ladder, the two-children-different-difficulty result, the invisible beat), you've gone too far.

---

## P1-1 — Anchor mastery to the difficulty *delivered*, not the difficulty *requested* (the one real defect)
**Problem:** `demand_for` clamps `quantity` to the objective's `COUNT_CEILING` but leaves `Demand.difficulty` at the requested value. That stamped value is what `learning_tag_for` writes and what `observation()` reads back — so a `count_to_5` beat asks for five frogs at stamped difficulty 0.55, 0.65, 0.75, 0.85 alike, and a clean win on the *identical five frogs* yields observations climbing to 1.00. The estimate rises on a number that changed nothing about the ask. Reachable in a first sitting. It inflates `mastery` (shown to parents Day 12) and trips `PREREQUISITE_MASTERY = 0.6`, promoting children up the counting chain faster than their evidence warrants.

**Fix (small, in the module's own idiom):** after clamping `quantity`, clamp `Demand.difficulty` *down* to the top of the difficulty band that actually yields that quantity — so `Demand.difficulty` always describes the ask that was made. This is the mirror of the quantity clamp `test_an_objective_never_asks_for_more_than_its_own_ceiling` already reasons about.

**Test (add the invariant):** for any objective and any input difficulty, two demands with equal `quantity`/`gap`/`novel_word` must carry equal `difficulty`. The new test must fail without the clamp and pass with it.

**Do not:** change the mastery update math, `COUNT_LADDER`, `COUNT_CEILING`, `ADAPT_STEP`, or the bottom-of-ladder behaviour the review confirmed correct. Only stop `difficulty` from describing an ask that was never made.

## P1-3 — Add a warn-only leakage probe over real generated beats
**Problem:** Invisibility is enforced only at the prompt layer and asserted only against `ScriptedAIProvider` (written to avoid lesson words) — so no check proves a *real* model doesn't echo `Primary objective: count_to_5` or `Target difficulty: 0.6` back into narration. This codebase already has the pattern: `_log_consequence_misattribution`, `_log_choice_distinctness`.

**Fix:** add a sibling warn-only quality probe that fires when `beat.narration` or `interaction.prompt_audio` contains the objective code, the difficulty string, or any of `lesson / practise / practice / quiz / test / score / correct answer`. Warn-only, same shape as the existing probes — it must **not** block or alter a beat, only log. One test that a leaky beat triggers the warning.

**Do not:** make it raise, gate, or mutate the beat. It is an observability probe, not a new safety gate.

## P1-2 — Guard against the per-session mastery gate dropping a second adventure's evidence (latent, not reachable today)
**Problem:** `update_from_session` stamps `PlaySession.learning_updated_at` and returns `"already"` on any later delivery. Today one session = one adventure, so this is **not reachable now** — but if a sitting ever spans two stories, the first `end` stamps the session and the second adventure's evidence is silently lost (worse than the memory app's parallel case, which loses only attribution and documents it).

**Fix (low-risk, minimal):** prefer gating idempotency **per adventure** if that is a small change consistent with `engine.update_from_adventures` (which is already written for several). If it is *not* small, do the minimum instead: add a docstring note next to the `learning_updated_at` argument in `tasks.py` naming the session-cap boundary, so the day a multi-adventure session lands this is found by reading rather than by a mastery number that quietly stopped moving. **Do not** refactor the session model or the happy-path idempotency the review verified byte-identical.

## P1-4 — Record interest-fit status, do NOT build it
**No code change.** `INTEREST_WEIGHT = 0.25` scores 0.0 for every realistic parent-entered interest (dinosaurs, space, trains…), so §10.3's third term is nominal today. The design and the seam are correct — this is the Day-11 interest-graph landing site. Action: ensure the module comment still names this as the Day-11 seam (it does per the review), and when tuning any other selector weight in this pass, do **not** treat interest fit as an operative term. Nothing to implement. (Building the interest graph is Day 11 — out of scope.)

---

## Bundled trivial cleanup (do only if zero-risk)
- **P2-2 — delete the vacuous assertion.** `test_mastery.py:428` `assert row.mastery < Objective.objects.count()` compares a 0–1 float to 7; it can only fail if the catalog is emptied. The next line (`0.0 <= row.mastery <= 1.0`) is the real check. Delete the vacuous line only.

**Explicitly out of scope for this pass** (log for later / Day 11): P2-1 (gating the 2nd ask to `total_beats>=6` — wants a real-model playtest first), P2-3 (warm-close difficulty drift — harmless), P2-4 (`expected` on the wire — by design), P2-5 (untracked `Docs/Day10-Build.txt` + `temp/` — founder housekeeping). Do not touch these.

---

## Re-verify before you stop (done when ALL true)
1. P1-1 clamp in, with the invariant test failing without it and passing with it.
2. P1-3 warn-only probe in, with a test that a leaky beat warns and a clean beat does not; confirmed it never blocks or mutates a beat.
3. P1-2 addressed (per-adventure gate if small, else docstring note); happy-path idempotency unchanged and still byte-identical on redelivery.
4. **No regressions:** two-children-different-difficulty still holds; the invisible beat still carries no `learning` key on the wire; the bottom-of-ladder mastery behaviour is unchanged; new-child/degraded fallbacks still produce a beat.
5. `make lint && make test` green (backend + frontend), `mypy` at its pre-existing baseline (no new errors), no new pending migrations.
6. CI green. Commit: `Day 10 fix: anchor mastery to the delivered ask + real-model leakage probe`.

## Do NOT do
- ❌ Do not change the mastery math, selector scoring, ZPD/spacing/prerequisite logic, or `COUNT_LADDER`/`COUNT_CEILING`.
- ❌ Do not build the interest graph or any Day 11 work.
- ❌ Do not make the P1-3 probe a blocking gate.
- ❌ Do not start P2-1/2-3/2-4/2-5.
