# Review — Day 12: Parent Dashboard — Reports, Insights & Transparency

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day12-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day12.md` + PRODUCT.md §12/§18.7/§10.6/§13.9. Verdicts with evidence.

## 1. Method
Play sessions, then read the generated reports and **cross‑check every claim against what actually happened** (the honesty test — the biggest risk here). Open the transparency log and confirm it shows the real stories verbatim. Attempt to read another parent's child (must be blocked). Scan insights for grades/percentiles (should be none).

```bash
curl -s :8000/api/v1/children/<id>/reports/weekly -H "Authorization: Bearer $ACCESS" | jq
curl -s :8000/api/v1/adventures/<id>/transcript -H "Authorization: Bearer $ACCESS" | head
# with parent B's token, request parent A's child report/transcript -> expect 403
cd backend && pytest apps/analytics -q
```

## 2. Checklist (1:1 with Day 12 acceptance criteria)
1. Per‑session report: specific, honest, with a **real** highlight (not generic boilerplate).
2. Weekly report aggregates the §18.7 fields correctly (validate the JSON shape).
3. Insights per‑domain, plain language, milestone‑mapped, **no grades/percentiles**.
4. Transparency log shows verbatim stories, **owner‑scoped** (cross‑parent blocked → 403).
5. Report claims match real session data — **no fabrication** (spot‑check several claims).
6. Dashboard IA + calm theme correct across pages.
7. lint/test/CI green.

## 3. Cross-cutting checks
- **Honesty (§12.1/§13.9):** the biggest risk is hype/fabrication. If a report claims something that didn't happen → **P0** (trust‑destroying).
- **Privacy:** transparency + reports strictly owner‑scoped; no cross‑family leakage.
- **Tone (§12.3):** insights supportive/developmental, not anxiety‑inducing report cards.
- **Scope discipline:** no settings/billing front‑run; no child‑facing changes.

## 4. Report to write → `docs/Review/reports/Day12-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 12 Review Report — Parent Dashboard". Include a **"claims vs. reality"** spot‑check subsection as evidence for criterion 5.

## 5. Verdict rules
**NOT‑READY** if any P0: fabricated/inflated report content, transparency leaks across families, grades shown, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 7 ✅.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 13.
