# Review — Day 11: Personalization Depth

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day11-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day11.md` + PRODUCT.md §11.7/§8.3/§15.1/§11.6. Verdicts with evidence.

## 1. Method
Create two children with different loves; play each and **compare the worlds/stories side by side**. Confirm `InterestEdge` weights move from behavior (and decay). Revisit a place/character across sessions to confirm persistence + consistency. Verify the fear ceiling holds even when interests would pull otherwise.

```bash
cd backend && pytest apps/memory apps/story -q
python manage.py shell -c "from apps.memory.models import InterestEdge as IE; print(list(IE.objects.values('topic','weight')))"
# play child A (dinosaurs) and child B (ocean); capture both adventures for side-by-side compare
```

## 2. Checklist (1:1 with Day 11 acceptance criteria)
1. `InterestEdge` weights update from behavior (raise chosen topics; decay others) — evidence.
2. Planner prefers high‑interest topics + reuses recurring characters/known places.
3. **Two profiles → visibly different worlds/stories** for the same objective (attach both; differences structural, not a swapped noun).
4. **≥3 distinct world palettes** render matching mood; night‑friendly variant works.
5. Recurring character/place persists + reappears **consistently** across two adventures.
6. Fear ceiling respected in personalized content.
7. lint/test/CI green; no regression to Day 6 (consistency) / Day 7 (safety) / Day 9 (memory).

## 3. Cross-cutting checks
- **Is it really different, or superficially reskinned?** Compare structure/characters/world, not just a swapped noun — a reskin is 🟡.
- **Consistency held (§8.2):** recurring entities still look/behave consistently after personalization changes.
- **Safety held (§11.6):** interest‑chasing never breaches the ceiling — a breach is **P0**.
- **Scope discipline:** no parent dashboard / companion‑evolution front‑run (stage 0).

## 4. Report to write → `docs/Review/reports/Day11-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 11 Review Report — Personalization Depth". Attach the two children's adventures as side‑by‑side evidence for criterion 3.

## 5. Verdict rules
**NOT‑READY** if any P0: two profiles produce basically the same world, fear ceiling breached, consistency/safety/memory regression, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 7 ✅.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 12.
