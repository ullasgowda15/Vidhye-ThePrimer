# Review — Day 15: Eval Harness, Hardening & Alpha Launch (M2)

> **Session type:** REVIEW — this is the **launch gate**, so be maximally rigorous. Independent auditor. Gather evidence, then write `docs/Review/reports/Day15-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day15.md` + PRODUCT.md §19.7/§16.10/§18.8/§6.1. Verdicts with evidence.

## 1. Method
Force an eval regression and confirm CI **blocks** the deploy. Run the end‑to‑end red‑team against the live loop. Actually perform a **restore drill** and confirm data returns. Trip the budget breaker and confirm warm fallback (not error). Walk the **entire §6.1 loop** in the production/staging alpha as a real family.

```bash
pytest evals/ -q                      # must be a REQUIRED, gating CI check
# introduce a known-bad prompt/model change -> confirm CI red -> deploy blocked -> then revert
# restore drill: restore latest backup into a scratch DB; verify row counts / a known record
# trip the inference-budget breaker; confirm child sees warm fallback, not an error
```

## 2. Checklist (1:1 with Day 15 acceptance criteria)
1. Eval suite (golden + red‑team + rubric + regression) **gates deploy** (prove a forced fail blocks).
2. End‑to‑end red‑team: no injection/jailbreak/ceiling breach succeeds live.
3. Backup **restored** in a drill (data verified back), not merely configured.
4. Feature flags + prompt versioning enable a **demonstrated** rollback.
5. Rate limits + budget breaker trip → warm fallback, not error.
6. Every child‑UI error path → safe fallback (no raw errors to a child).
7. Production alpha live; **10–25 families** onboarded; monitoring + incident queue + guardrail metrics active.
8. Full regression + safety pass; lint/test/CI green. **M2.**

## 3. Cross-cutting checks (the launch gate)
- **§6.1 loop end‑to‑end in production:** onboard → personalized illustrated voiced adventure → next‑day remembered greeting → parent report. If any link is broken, **M2 is NOT met**.
- **Evals provably gating:** the eval suite must be a **required** CI check, not advisory.
- **Reliability (§16.10):** the restore actually works; memory durability (the core promise) is protected.
- **Regression sweep:** re‑confirm Day 7 safety, Day 9 memory (no cross‑child leak), Day 13 settings enforcement still hold.

## 4. Report to write → `docs/Review/reports/Day15-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 15 Review Report — Eval Harness, Hardening & Alpha Launch (M2)". Add a **"Launch readiness"** subsection with an explicit **GO / NO‑GO** recommendation and the §6.1 loop walkthrough as evidence.

## 5. Verdict rules
This is a **launch gate** — default **NO‑GO** unless everything is proven. Any of these is **P0**: evals not gating, any successful attack, restore drill fails, an error path shows a child a raw error, the §6.1 loop broken anywhere, cross‑child memory leak, a setting unenforced, CI red. **SHIP (GO)** only if all 8 ✅ and the loop walkthrough passes. When in doubt, **NO‑GO** — real children are about to use this.

## 6. After writing
Print a 5‑line summary with the explicit GO/NO‑GO and any P0s. Do not fix; the sprint's build/review loop ends here (next is the Month‑2 roadmap, PRODUCT.md §20.4).
