# Build — Day 5 FIX: Consequence fidelity (close M1)

> **Session type:** BUILD (fix pass for Day 5). Read this whole file first, fix everything in scope, run the **Self-review & evaluation** in §7, then do the **Commit & push** in §9. Do **not** start Day 6.
>
> **Inputs:** this file · `docs/Build/Day5.md` (original brief + criteria) · `docs/Review/reports/Day5-report.md` (the audit — file/line hints below are from it). **Source of truth:** `docs/PRODUCT.md` §8.5 (choices/consequences), §8.8 (warm close + tomorrow hook), §14.5 (2–3 distinct options).
>
> **Why this pass exists:** Day 5 reviewed **NOT-READY (one P0)**. Choices are **not** cosmetic — the controlled A/B proved material divergence — but ~21% of consequence beats **narrate an action the child did not take** (tapped "Count the spiders" → told *"Spidey chose to point at the toy elephant"*). That breaks §8.5 and blocks M1. This pass closes it and two P1s that ride with it.

---

## 1. Scope of this pass
1. **P0** — the consequence beat must always name the child's **actual** choice; the plan stage must never override it.
2. **P1-a** — the warm close must always plant a tomorrow hook.
3. **P1-b** — the 2–3 choices must be genuinely **different next-actions** (this also shrinks the P0 surface).
4. **Fold** — stamp `Beat.model`; normalise the outgoing `interaction` frame; add one real-saved-row legacy test.

**OUT of scope:** the learning engine / ZPD / difficulty (Day 10), the answered-value-into-summary and `consequence_summary`-render items (Day 8/10), and the 120s image-outage dead-turn (already carried into `docs/Build/Day6.md`). Don't touch them.

## 2. P0 — the child's action is the fact; the plan stage bends
Root cause (report): `build_beat_context` gives the model **both** a fixed plan stage for this `seq` (`orchestrator.py:416-417`) **and** `CONSEQUENCE_RULE`'s "name what they chose". When the tap doesn't point at the stage, the stage wins and the model rewrites the child's action — sometimes literally with "you chose to".

**Do (all three — cheapest first, but land them together):**
- **Reframe the plan stage in the prompt** as a *destination to move toward from where the child has just taken us*, **not** a script for this beat. State explicitly: *if the child's action and the stage disagree, the child's action is what happened; bend the stage toward it.* The stage informs *where we're heading*, never *what the child just did*.
- **Constrain the acknowledgement:** forbid the construction "you chose to X" / "{name} chose to X" unless `X` is the label in the prompt's `Chose:` line (the label is right there). The consequence must name the **actual** tapped label (paraphrase is fine; a *different* action is not).
- **Add a cheap post-write guard (log, don't block):** after a consequence beat is written, if its narration names an **un-chosen** option's label (all labels are on the row) while omitting the chosen one, emit `story.consequence_misattributed` with adventure/beat/seq. Do **not** block or regenerate — the point is to make the rate visible (and testable) instead of parent-discovered.

## 3. P1-a — the close always plants a tomorrow hook (§8.8)
`normalise_choices(is_final=True)` enforces the close's *shape* (`type:none`, no cards) but nothing enforces the hook; `FINAL_BEAT_RULE` merely asks, and the model complied 0/5 on the configured model. **Do:** make the hook a **named, demanded final sentence** — instruct the model to end the close with one sentence beginning from *what the companion will look for / do together tomorrow* (something it "found or noticed"). A regenerate-on-hookless validator is the expensive version and not required; prefer the prompt fix. Add a test asserting the close carries a forward-looking final sentence.

## 4. P1-b — choices are different things that happen next (§14.5)
Live beat 3 offered "Count the left / middle / right frog" — three labels for one action, which *guarantees* misattribution. `CHOICE_RULE` says options should differ; nothing checks it. **Do:** strengthen the rule so each option must lead somewhere the **others do not** (different next-action, not a cosmetic variant), and log a warning when 2–3 option labels share a leading verb / obvious paraphrase so the rate is visible. Prompt-side + log is enough; no hard block.

## 5. Folds (cheap, do them here)
- **`Beat.model`** — add the column + migration and stamp the provider/model that produced each beat (the whole P0 failure class is model-dependent, and it's un-backfillable later). Populate on write.
- **Frame contract** — `beat_payload` ships `interaction` verbatim (`streaming.py:156`), so a legacy beat puts bare-string `choices` on the wire while the top-level `choices` carries coerced objects, contradicting the `sse.ts:57` type. **Normalise the interaction blob on the way out, or drop `choices` from it** (the top-level field supersedes). This closes a latent re-open of the bug `21a9a4d` fixed.
- **One real legacy test** — the `21a9a4d` tests use *unsaved* `Beat(...)` objects (the exact "synthetic, not a real row" the brief warned against). Add a test that **saves** a beat with bare-string `choices` and drives `streaming.stream_beat` over it, asserting `: open → beat → done` with coerced options and no exception.

## 6. Do NOT do
- ❌ Don't start Day 6. ❌ Don't add a learning engine / ZPD / difficulty adaptation. ❌ Don't hard-block or regenerate on the misattribution/choice-similarity guards — **log only**. ❌ Don't weaken the no-fail path, the count-beat stand-in, `normalise_choices`, or the compaction. ❌ Don't commit `.env` or `backend/media/`.

## 7. Self-review & evaluation (REQUIRED before you stop)
Do not declare done from the suite alone — the P0 lives in model output. Re-verify against reality and **write a short self-evaluation** in your final message rating each item **Verified / Partial / Unverified** with evidence:
1. `make lint && make test` green; `Beat.model` migration present; `makemigrations --check` clean.
2. **Re-run item 12's controlled A/B** on the configured model (one plan, one beat 1, beat 2 twice varying only the tap) — confirm divergence still holds and **neither branch narrates the un-chosen action**.
3. **Re-measure the misattribution rate:** generate ≥12 real consequence beats where the tap does **not** match the plan stage, and report how many misreport the child's action. M1 is clear only if this is **~0** (and the guard logged any that slipped). State the number.
4. Play one adventure to the warm close and confirm it plants a tomorrow hook; report the close text.
5. Confirm choices at a decision point are three *different* next-actions, not one action three ways.
6. Legacy: the new saved-row test fails if `Beat.options` regresses; the outgoing frame no longer contradicts its type.
7. State plainly what you could **not** verify.

## 8. Acceptance criteria (done when ALL true)
1. Consequence beats name the child's **actual** tapped label; the plan stage never overrides it; the misattribution rate on off-stage taps is ~0 (guard logs any residual).
2. The warm close always plants a tomorrow hook (test-pinned).
3. Choices at a decision point are genuinely distinct next-actions; near-duplicate options are logged.
4. `Beat.model` records the producing model on every beat.
5. The outgoing `interaction` frame matches its declared type; a saved legacy row streams cleanly (new test).
6. `make lint && make test` green; item 12 A/B + misattribution re-measure show M1 is genuinely met on the configured model.
7. Self-review & evaluation (§7) written honestly, **including the measured misattribution number**.

## 9. Commit & push (do this last)
1. `git status` — confirm no `.env`, no `backend/media/…`, no key material staged.
2. Stage the fix + migration + the untracked `docs/Build/Day5-fix.md`. `git add -A` is safe given the gitignore; eyeball `git status` first.
3. Commit, e.g. `Day 5 fix: consequence fidelity, tomorrow hook, distinct choices, model stamp`.
4. `git push`; confirm the Actions run for the new commit is **green**.

## 10. End state
Consequence beats always name what the child actually did (plan stage is a destination, not a script; false "you chose" forbidden; residual misattribution logged and ~0), the warm close always plants a tomorrow hook, choices are distinct next-actions, every beat records its model, and the frame contract no longer lies. **M1 is genuinely met on the configured model** — verified by re-running the A/B and re-measuring the misattribution rate, not by a green suite. Ready for M1 sign-off or a scoped re-review, then Day 6.
