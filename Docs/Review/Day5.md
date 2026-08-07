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

## 3. Cross-cutting checks
- **Do choices actually matter?** Verify divergence is real (materially different consequence beats + recorded `consequence_summary`), not cosmetic word‑swaps.
- **Emotional safety (§8.5/§8.8):** confirm zero shame/lose framing; endings always warm. This is a **values gate**, not a nicety — a fail state is P0.
- **Prompt economy (§8.6):** the summary genuinely **compacts** (replaces old beats), not just appends — else cost/latency balloon later.
- **Scope discipline:** no memory/learning/safety‑hardening front‑run; no child‑visible scores/timers.

## 4. Report to write → `docs/Review/reports/Day5-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 5 Review Report — Beat Loop, Choices & Consequences (M1)". State explicitly whether **M1** is genuinely met, with the two divergent playthroughs as evidence.

## 5. Verdict rules
**NOT‑READY** if any P0: choices don't affect the story, **any fail state exists**, endings not warm, prompt grows unbounded, adventure can't complete, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 8 ✅ and M1 truly met.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 6.
