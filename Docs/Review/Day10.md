# Review — Day 10: Learning Engine v0 (Invisible Curriculum)

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day10-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day10.md` + PRODUCT.md §10/§8.9–8.10. Verdicts with evidence.

## 1. Method
Play sessions and **read the beats as a child would** — is the lesson invisible? Confirm the selector picks ZPD objectives and interleaves across sessions. Deliberately struggle → confirm scaffolding. Check `mastery` + `due_at` update from evidence. Compare two children at different levels.

```bash
cd backend && pytest apps/learning -q
python manage.py shell -c "from apps.learning.models import LearningProgress as LP; print(list(LP.objects.values('objective__code','mastery','due_at')))"
# play 2+ sessions; confirm objective selection interleaves and respects spacing
```

## 2. Checklist (1:1 with Day 10 acceptance criteria)
1. `Objective`/`Milestone` catalog seeded (numeracy + vocab) mapped to milestones with `standard_ref`.
2. Selector picks a ZPD objective with spacing + interleaving (cross‑session evidence).
3. Objective embedded **invisibly** (reads as story, not a lesson); beats carry the `learning` tag.
4. Within‑session difficulty adapts (scaffold on struggle → `assisted=True`; stretch on ease) — both demonstrated.
5. `mastery` updates from behavioral evidence; `due_at` advances; **no quiz**.
6. Two children at different levels → different `difficulty` for the same objective.
7. lint/test/CI green incl. mastery‑moves + objective‑exercised test.

## 3. Cross-cutting checks
- **Invisibility gate (Principle #1):** if any beat reads like a worksheet/quiz/grade → **P0** (the whole thesis is "never feels like school").
- **Assessment authenticity (§10.5):** evidence is behavioral (latency/retries/assisted), not an explicit test.
- **Model interpretability:** the mastery estimate is simple/explainable (parents see it Day 12).
- **Scope discipline:** no parent insights, no extra domains, no child‑visible scores.

## 4. Report to write → `docs/Review/reports/Day10-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 10 Review Report — Learning Engine v0". Quote a learning beat as evidence that the objective is invisible.

## 5. Verdict rules
**NOT‑READY** if any P0: lesson visible / feels like school, mastery doesn't update, no ZPD/adaptation, child sees scores, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 7 ✅.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 11.
