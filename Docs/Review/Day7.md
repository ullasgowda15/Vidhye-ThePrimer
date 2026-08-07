# Review — Day 7: Safety Layer v0 + Week-1 Close

> **Session type:** REVIEW — and on safety, be **maximally skeptical**. Independent auditor. Gather evidence, then write `docs/Review/reports/Day7-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day7.md` + PRODUCT.md §13/§19.6–19.7. Verdicts with evidence.

## 1. Method
**Actively attack it.** Run the injection corpus against child‑input **and** profile fields. Request content beyond the intensity ceiling and on avoid‑topics. Force a hard‑fail and confirm a **warm fallback** (not an error) reaches the UI. Read the code path to confirm the output classifier is a **separate** call from generation. Confirm incidents are logged for every non‑pass.

```bash
cd backend && pytest apps/safety/redteam -q       # injection + ceiling + unsafe-topic bait
# manual probes via API: inject into a story free-text field AND into a child's profile/avoid_topics
# force guard_output -> block, confirm a warm fallback beat renders (no error UI)
# open /admin/ safety incident queue; confirm rows + severities
```

## 2. Checklist (1:1 with Day 7 acceptance criteria)
1. Input guard + **independent** output classifier + image scan wrap **every** beat type (open/consequence/close) — not just beat 1.
2. Unsafe request blocked/rewritten; a `block` → warm, child‑indistinguishable fallback.
3. Injection via child input **AND** profile neutralized (evidence from the corpus).
4. `escalate` path de‑escalates + logs high‑severity + fires the parent‑notify seam.
5. Every non‑pass writes a `SafetyIncident`; the admin queue shows them with severity filters.
6. Image scan blocks an unsafe image → safe substitute (status recorded).
7. Week‑1 debt cleared (Day1–6 P0/P1 resolved); full loop + CI green.
8. Red‑team suite passes; lint/test green.

## 3. Cross-cutting checks
- **Independence (§13.3):** confirm the classifier is NOT the generating model grading itself. If it is → **P0**.
- **Coverage:** guard on opening AND consequence AND close beats — partial coverage → **P0**.
- **No spinner on fallback:** blocked generations still feel like normal warm beats (no error UI, no spinner).
- **Honesty:** if you couldn't fully exercise a path (e.g. no key), mark ⚪ and say so — **never assume safety works**.

## 4. Report to write → `docs/Review/reports/Day7-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 7 Review Report — Safety Layer v0 + Week‑1 Close". Add an explicit **"Attacks attempted vs. outcome"** subsection (each probe → what happened).

## 5. Verdict rules
On this day, **default to NOT‑READY unless safety is convincingly proven.** Any of these is **P0**: self‑grading classifier, partial guard coverage, an injection that succeeds, an unsafe beat reaching the UI, missing incident logging, CI red. **SHIP** only if all 8 ✅ and the attack subsection shows real, passed probes.

## 6. After writing
Print a 5‑line summary emphasizing the safety verdict. Do not fix; do not proceed to Day 8.
