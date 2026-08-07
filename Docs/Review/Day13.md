# Review — Day 13: Settings, Controls & Wellbeing

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day13-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day13.md` + PRODUCT.md §12.7–12.8/§13.4/§13.6–13.8. Verdicts with evidence.

## 1. Method
Set restrictive settings (avoid‑topic, low ceiling, short cap) and **try to make the system violate them** through play. Confirm the cap ends warmly. **Grep/inspect the codebase** for any child‑directed notification/streak/FOMO mechanic. Exercise export + delete and confirm full cascade.

```bash
curl -sX PATCH :8000/api/v1/children/<id>/settings -H "Authorization: Bearer $ACCESS" \
     -d '{"avoid_topics":["spiders"],"intensity_ceiling":1,"session_cap_min":5}'
# play; attempt to elicit spiders / high intensity; confirm never appears; confirm warm cap end
grep -rEi "streak|notif|badge|fomo|comeback|remind.*child" frontend/src backend/apps || echo "none found (good)"
curl -sX POST :8000/api/v1/account/export -H "Authorization: Bearer $ACCESS" -o export.zip
cd backend && pytest apps/profiles apps/safety -q
```

## 2. Checklist (1:1 with Day 13 acceptance criteria)
1. Each setting honored immediately in **both** generation **and** the safety guard (avoid‑topic absent; ceiling respected — prove by trying to breach).
2. Session cap → **warm** close; time window enforced.
3. **Zero** child‑directed notifications/streaks/FOMO (inspection evidence — paste the grep result).
4. Accessibility toggles work in the child UI.
5. Export returns portable data; delete (account + child) cascades fully (no orphans).
6. Privacy disclosure plain‑language + accurate.
7. lint/test/CI green incl. avoid‑topic + warm‑cap test.

## 3. Cross-cutting checks
- **Enforcement, not display:** a setting shown in the UI but ignored by generation/safety → **P0**.
- **Ethical line (§13.6):** ANY child‑directed pull mechanic → **P0** regardless of "engagement" upside.
- **Delete completeness:** no orphaned child data after deletion (privacy/COPPA).
- **Scope discipline:** no billing/eval front‑run.

## 4. Report to write → `docs/Review/reports/Day13-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 13 Review Report — Settings, Controls & Wellbeing". Include a **"tried to violate settings → outcome"** subsection.

## 5. Verdict rules
**NOT‑READY** if any P0: a setting is ignored by generation/safety, a child‑directed pull mechanic exists, delete leaves orphans, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 7 ✅.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 14.
