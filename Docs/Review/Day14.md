# Review — Day 14: Analytics, Cost, Accessibility & Polish

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day14-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day14.md` + PRODUCT.md §22/§5.6/§19.9/§14.6. Verdicts with evidence.

## 1. Method
Generate seeded activity, then read the metrics + cost/session off the dashboard and **sanity‑check them against the §22.3 definitions**. Confirm product analytics ≠ parent view (no raw child behavior leaking to parents). Toggle **every** accessibility option and verify. Run onboarding fresh with a critical eye and a keyboard‑only pass on the parent UI.

```bash
cd backend && pytest apps/analytics -q
# open the internal metrics dashboard; verify activation/day-2/W1/completion/adventures-per-week + cost/session
# confirm the parent-facing endpoints expose ONLY the curated subset, not raw events
```

## 2. Checklist (1:1 with Day 14 acceptance criteria)
1. Events flow; activation / day‑2 / W1 / completion / adventures‑per‑week computable (**show the numbers**).
2. Per‑generation cost traced (LLM + image + TTS); **cost/session** visible internally.
3. Product analytics vs. parent curated subset **separated** (no raw leakage).
4. Accessibility: captions/reduced‑motion/high‑contrast/dyslexia‑font/audio all work; parent UI WCAG AA basics (keyboard + contrast + focus).
5. Onboarding/first‑run polished.
6. lint/test/CI green.

## 3. Cross-cutting checks
- **Metric definitions (§22.3):** do computed metrics match the definitions (e.g. activation = onboard + first‑story complete)? Wrong definitions mislead the founder → 🟡/P1.
- **Cost realism (§22.4):** is cost/session plausible and **complete** (all of LLM + image + TTS)? Any untracked cost source → 🟡.
- **Privacy boundary (§5.6):** raw child behavioral data must not surface in the parent UI → leakage is **P0**.
- **Scope discipline:** no billing/eval front‑run; no new features.

## 4. Report to write → `docs/Review/reports/Day14-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 14 Review Report — Analytics, Cost, Accessibility & Polish". Paste the computed metrics + cost/session as evidence.

## 5. Verdict rules
**NOT‑READY** if any P0: metrics wrong/absent, cost untracked, raw child data in parent view, accessibility broken, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 6 ✅.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 15.
