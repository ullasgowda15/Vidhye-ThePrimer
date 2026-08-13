# Fix Brief — Day 1–15 Audit Punch-List

> **Hand this to a fresh Claude Code session (in `cmd`, repo root).** It closes the findings in `docs/Audit/Day1-15-audit-report.md`. This is a **child-safety and privacy** fix pass before a real family sees the product — hold the bar at "proven, not plausible".
>
> **Order:** the three P0s first (F-1, F-2, F-3), then the five P1s (F-4–F-8). The P2 quick wins at the end only if they cost nothing.

---

## Golden rule

**Fix only the findings named below. Do not touch anything the audit marked clean in §4 of the report** — cross-account isolation, deletion completeness, ceiling enforcement, the fallback, the budget breaker, the boot guards, the ship gate, telemetry shape, no-secrets. Every one of those was proven by probe; leave it exactly as is. Each fix below is additive (a scrub, a fence, a guard, a corrected sentence). After each, re-run the audit's own repro for that finding and confirm it now fails to reproduce, and re-run `make test && make evals` to confirm no regression.

---

## P0-1 — Put companion creation through the safety layer (F-1, F-2, F-10)

**Problem.** The onboarding/companion path is the only generation path that never enters `apps.safety.pipeline`. A child-typed `companion.name` (barely validated by design) and the model-generated `descriptor` reach two **unfenced instruction prompts** verbatim: the descriptor prompt (`companion/descriptor.py:87-99`) and every illustration prompt (`media/prompts.py:78-85` via `companion/service.py:130-142` and `render_scene`). The descriptor is generated once and **persisted forever**, so an injection here is a permanently poisoned companion.

**Fix.**
1. In `descriptor.build_context`, route `companion.name` through the same instruction-layer treatment story text gets — `safety.scrub_profile_field` / `assembler._instruction_text` — and fence the variable block in a `<companion kind="data">` wrapper using the existing `prompting.layers.DATA_PREAMBLE`. Do **not** rely on the 40-char length cap.
2. In `CharacterRef.render()` (`media/prompts.py`), scrub **both** `name` and `descriptor` in that one place, so every caller (reference set, beat, dev commands, smoke tests) is covered and the cache key derives from the scrubbed text. Note this changes `character_signature` and invalidates the existing illustration cache once — that is the correct cost.
3. Add `companion_name` and `companion_descriptor` to `corpus.INJECTION_SURFACES`, and make `evals/test_redteam.py` drive them through the **descriptor** and **illustration** prompt builders — not only through `assembler.assemble`. The gate must be "every surface × every prompt builder"; a builder not in the sweep has no gate.

**Tests.** Plant `scrub.py`'s `new_instructions` pattern in a companion name and assert no directive survives into either prompt (fail-without-fix, pass-with). The new eval surfaces must be red before the fix and green after.

**Do not.** Widen `scrub.py`'s patterns (the Day-15 argument against that stands — it would eat "pretend to be a dragon"). Don't change what a child can name their companion; only where that name is allowed to land.

---

## P0-2 — Stop child speech reaching `:free` routes, and make it impossible to restart (F-3)

**Problem.** `AI_BEAT_MODEL`, `AI_SAFETY_MODEL` and (by inheritance) `AI_MEMORY_MODEL` are all `:free` OpenRouter slugs. The memory prompt carries the verbatim session digest. `:free` routes permit training/logging — contradicting the disclosure ("we never use your child's data to train anybody's models") and PROGRESS.md's §13.8 rule. `launch_check` passes it because it only checks reachability and separateness.

**Fix — two halves; the second matters more.**
1. **Repoint the models** (config): safety, memory, and embeddings onto **paid, data-excluded** routes. **Decision from the founder (bake this in):** the beat model *also* carries child data (name, fears, memory bundle), so for this product's disclosure the beat model goes on a paid data-excluded route too — **all four child-data models are paid/data-excluded.** Update `.env`, `docs/Hosting.md §4`, and `docs/Runbooks/Deploy-GCP.md §7` to match (remove the "beats on the free Gemini tier" line).
2. **Make it enforceable** (code): give `apps.common.model_ids` a `permits_training(model)` / `is_free_route(model)` rule; in `prod.py`'s boot guards, **refuse to boot** if any of the safety/memory/embedding/beat models is a `:free` route; and add a **blocking** `launch_check` row (not a reachability one) that fails on a training-permitted route for any child-data model. The disclosure's "we will only use providers whose terms forbid training" promise then has something enforcing it.

**Tests.** A unit test that `prod.py` refuses a `:free` safety/memory/beat model; a `launch_check` test that the new row blocks. 

---

## P1-1 — The rollback lever must not collapse grader independence (F-4)

**Problem.** `ai.beat_model` (the no-deploy rollback) validates only reachability, not that it differs from `AI_SAFETY_MODEL`. Flipping it to the safety model's id silently makes the same weights write and grade the beat (§13.3 breach). `launch_check`'s `grader_is_separate` compares *settings*, not the flag, so it stays green.

**Fix.** Add one rule to `flags._validate_beat_model`: reject any value equal to `settings.AI_SAFETY_MODEL`, message naming §13.3. Also change `launch_check`'s `grader_is_separate` to read the **effective** model (`flags.value("ai.beat_model") or settings.AI_BEAT_MODEL`) so the preflight sees what the router sees. Assert alongside the Day-15 `test_no_flag_can_switch_off_a_safety_guard`.

---

## P1-2 — Make the disclosure true, sentence by sentence (F-5, F-6, F-7)

**This is the third time disclosure over-claims have been found — fix the log lines first, then the text.**

1. **F-7 (do first).** Drop `interests=` from the two log lines in `worlds.py:390-394` and `orchestrator.py:467-478` (the world key / count already carry the operational signal). For the account-deletion record (`accounts/views.py:412-419`), move it out of the log stream into an append-only deletion receipt holding a **hash** of the email, not the address — the accountability need is real, the retained PII is not.
2. **F-5 (backups).** The disclosure says "we do not keep backups yet"; a 205 KB dump sits in `backups/`. Rewrite to what's true using the Day-13 shape ("what is true today + a commitment with a trigger"): dumps are taken, held N days, encrypted, and a deleted family is never restored from one. Then make it enforceable — a retention/rotation step in `backup.sh` and a `launch_check` row reading the newest dump's age against the stated number.
3. **F-6 (log retention).** "Raw activity logs are kept briefly and then discarded" is unimplemented. Once F-7 is done (no child data in logs), either state the platform's real retention or soften the sentence to what's true.

**Do not** invent claims. Every sentence in `privacy.py` must be checked against code — that's the file's own standard.

---

## P1-3 — Dependency audit + take the upgrades (F-8)

**Problem.** Pinned to vulnerable versions (Django 5.1.5 — 26 advisories; `cryptography` 44.0.0 — 8; `simplejwt` 5.4.0 — 1; `react-router` moderate advisories), and CI never audits deps. `cryptography` and `simplejwt` are load-bearing for the disclosure's own guarantees.

**Fix.** Add a `deps` CI job (or step) running `pip-audit` and `npm audit --audit-level=high` — blocking before families. Take the upgrades: Django to the 5.1.x tip (at least), `cryptography` and `simplejwt` to their fix versions, `npm audit fix` for the frontend. Re-run `make test && make evals` after (both green today — the right regression net for a dependency bump).

---

## P2 — bundled quick wins (do only if zero-risk)

- **F-11** — add `frontend/src/components` (holds `SafeBoundary.tsx`, `SceneTransition.tsx`) to `CHILD_SURFACES` in the pull-mechanics scan, and raise the `> 12` floor. One-line, and it re-closes "the most important test in the day."
- **F-9** — the child landing screen renders the word "offline" on health-check failure (`ChildHome.tsx:69`). Drop the text (keep the `aria-hidden` coloured dot) or move it behind the "Grown-ups" boundary.
- **F-12** — add one clause to the `who_sees_it` disclosure naming what actually goes to providers (avoid-topics, fears, remembered things) — a stronger sentence, not a weaker one.
- **F-13** — correct the stale test counts in `PROGRESS.md` (1304 / 183 / 42+1).

**Out of scope this pass:** the confirmed known-open items in report §6 (weekly-report sitting count, `Personalization.fear_ceiling` docstring, timezone window, staff-tier `search_fields`) — log for later, do not touch.

---

## Re-verify before you stop (done when ALL true)

1. Each P0/P1 has a test that fails without the fix and passes with it; the audit's repro for F-1, F-2, F-3 no longer reproduces.
2. Companion name + descriptor are scrubbed and fenced on **both** the descriptor and illustration paths; the two new eval surfaces are green.
3. `prod.py` refuses a `:free` child-data model; the new blocking `launch_check` row works; `.env`, `Hosting.md`, `Deploy-GCP.md` updated to all-paid/data-excluded.
4. `ai.beat_model` flag rejects the safety model; `grader_is_separate` reads the effective model.
5. Every `privacy.py` sentence is true against code; no child data (interests) in logs; deletion receipt holds a hashed email.
6. `pip-audit` / `npm audit` step in CI; the three backend upgrades taken; suite + evals green after.
7. **No regression to anything in report §4** — re-run the cross-account, deletion, ceiling, and fallback probes if practical, or at least `make test && make evals && make lint` all green, and `launch_check` behaves.
8. Commit with the real CI run id. Then write a short fix report (what changed, what was re-verified) so it can be checked, and note that the **`--live` eval pass and a real image-moderator exercise are still owed** before families (they gate F-1/F-2's downstream mitigation).

## Do NOT

- ❌ Touch anything the audit proved clean in §4.
- ❌ Widen `scrub.py` patterns or change what a child can name their companion.
- ❌ Add any disclosure sentence that isn't true against code.
- ❌ Start the deferred known-open P2s or any new scope.
