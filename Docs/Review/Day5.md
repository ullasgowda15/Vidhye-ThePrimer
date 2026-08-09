# Review — Day 5: Beat Loop, Choices & Consequences (M1)

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day5-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day5.md` + PRODUCT.md §8.5–8.8/§18.6. Verdicts with evidence.

## 1. Method
Play (or script) a full adventure **twice, making different choices**, and confirm the story diverges and reacts specifically (by name/consequence). Inspect persisted `Choice` rows. Measure prompt token size across beats to confirm the running‑summary bounds it. Deliberately "fail" a task and confirm scaffolding, not a fail state.

```bash
curl -sX POST :8000/api/v1/adventures/<id>/choice \
     -d '{"beatId":"...","choiceKey":"help_frog","latencyMs":1200,"retries":0}'
cd backend && pytest apps/story -q     # includes a full multi-beat playthrough test with 2 different choices
# log/inspect prompt token counts per beat to confirm running-summary compaction
```

## 2. Checklist (1:1 with Day 5 acceptance criteria)
1. 2–3 **illustrated** choices; the consequence beat **specifically** reflects the choice (not a generic continuation).
2. A full ≥4‑beat adventure plays to a warm close; `end` finalizes `Adventure` + `PlaySession`.
3. Each choice persisted with `latency_ms`/`retries`/`assisted` + `consequence_summary`.
4. Running‑summary compaction bounds prompt size (evidence: token counts stay roughly flat, not linear).
5. **No fail states** anywhere; a "wrong" interaction → gentle scaffolding (`assisted=True`).
6. Final beat always warm/safe + tomorrow hook.
7. Prefetch during current beat; no spinner.
8. lint/test/CI green incl. the 2‑choice full‑playthrough test. **M1.**

## 2b. In-session fixes to audit (from the Day-5 founder test — treat as UNreviewed)
The Day-5 founder test surfaced issues that were fixed *during* that session, so this is **new, un-reviewed generation-behavior code**. Audit it as rigorously as the original build — do not assume it's blessed. Commits `21a9a4d` (options coercion), `d6598c4` (admin static under ASGI), plus a possibly-just-committed `COUNT_RULE`/`asks_to_count`/`normalise_choices` change.

9. **Legacy choice shape can't kill the stream.** Pre-Day-5 beats held bare-string `options`; the new `{key,label,icon}` shape called `dict()` on them and threw *mid-stream* → a dead screen that replayed on reload (a §13 violation). Verify `Beat.options` now coerces: a mapping passes through, a legacy string becomes a label-only option, junk is dropped with a warning — and that this is covered by a test using a real legacy row, not a synthetic one. Confirm no un-normalised old beats can still reach the SSE path.
10. **The forced count beat is sound.** `COUNT_RULE`/`asks_to_count` makes exactly one `count` beat per adventure at its midpoint so criterion #5 is exercisable. Verify: it's **orchestrator-chosen (model-independent)**, fires once per adventure, and `normalise_choices` strips choice cards from `count`/`say` beats (the client selects the number pad by the *absence* of options — stray cards would remove the only control that can answer). Confirm it does not itself introduce a fail state, and that it's clearly a Day-10 **stand-in** (no skill model / ZPD), not a smuggled learning engine (scope).
11. **Admin static fix** (`d6598c4`) is DEBUG-guarded and doesn't serve static in prod (uvicorn) inappropriately.
12. **M1 on the configured model.** The founder test found choices **do not diverge** on `gemma-4-26b:free` (branch B narrated the un-chosen option — the child is told they did something they didn't) but **do** on `nemotron-3-super-120b:free`. Re-run the controlled A/B (one plan, one beat 1, beat 2 twice varying only the tap) on whatever `AI_BEAT_MODEL` is now configured, and **only pass criterion 1 / declare M1 if divergence holds on the configured model** — a green test suite does not prove this, since the divergence failure is in model output, not code.

## 3. Cross-cutting checks
- **Do choices actually matter?** Verify divergence is real (materially different consequence beats + recorded `consequence_summary`), not cosmetic word‑swaps.
- **Emotional safety (§8.5/§8.8):** confirm zero shame/lose framing; endings always warm. This is a **values gate**, not a nicety — a fail state is P0.
- **Prompt economy (§8.6):** the summary genuinely **compacts** (replaces old beats), not just appends — else cost/latency balloon later.
- **Scope discipline:** no memory/learning/safety‑hardening front‑run; no child‑visible scores/timers.

## 4. Report to write → `docs/Review/reports/Day5-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 5 Review Report — Beat Loop, Choices & Consequences (M1)". State explicitly whether **M1** is genuinely met, with the two divergent playthroughs as evidence.

## 5. Verdict rules
**NOT‑READY** if any P0: choices don't affect the story **on the configured model** (item 12), **any fail state exists**, a legacy/poisoned beat can dead-screen the child (item 9), endings not warm, prompt grows unbounded, adventure can't complete, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 12 ✅ and M1 truly met on the configured model. A green suite is **not** sufficient for items 1 and 12 — divergence lives in model output, so it must be shown live, not inferred from tests.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 6.
