# Review — Day 6: Illustration Consistency & Latency-Hiding

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day6-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day6.md` + PRODUCT.md §8.2/§16.6/§4.9/§14.7. Verdicts with evidence.

## 1. Method
Render a full adventure and **look at the images as a sequence** — is it the same companion throughout? Introduce a recurring character and confirm consistency on reappearance. Inspect a logged illustration prompt for injected style tokens + references. Pull cache hit/miss counts. Watch the UI seams for spinners/flashes; toggle reduced‑motion.

```bash
cd backend && python manage.py render_consistency <child_id> --beats 6   # eyeball the sequence
pytest apps/story apps/media -q
# inspect a logged illustration prompt; pull provider hit/miss counts for one adventure
```

## 2. Checklist (1:1 with Day 6 acceptance criteria)
1. Companion recognizably identical across every beat's illustration (attach the sequence).
2. Recurring character consistent across reappearance (beat 2 → beat 4), persisted + reference‑driven.
3. Illustration prompts provably include style tokens + character refs (paste a logged prompt).
4. High cache hit‑rate across an adventure (report counts); expressions pre‑warmed; provider calls/adventure dropped vs. Day 5.
5. **Zero spinners/blank flashes;** gaps covered by narration/motion via `SceneTransition`.
6. `prefers-reduced-motion` disables motion cleanly without breaking flow.
7. Prefetched media usually ready on transition (report typical wait); lint/test/CI green; no Day‑5 regression.

## 3. Cross-cutting checks
- **Consistency is engineered, not luck:** confirm references drive it (if refs were removed it should NOT stay consistent).
- **Cost (§22.4):** cache hit‑rate directly affects unit economics — a low hit‑rate is 🟡/🔵 even if visuals look fine.
- **Accessibility:** reduced‑motion genuinely disables transitions without a broken flow or a fallback spinner.
- **Scope discipline:** no new gameplay/memory/learning/safety front‑run; no regression to no‑fail/warm‑close.

## 4. Report to write → `docs/Review/reports/Day6-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 6 Review Report — Illustration Consistency & Latency‑Hiding". Attach the image sequence as primary evidence for criteria 1–2 and the cache counts for criterion 4.

## 5. Verdict rules
**NOT‑READY** if any P0: companion visibly inconsistent, spinner/flash visible to a child, references not injected, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 7 ✅.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 7.
