# Review Folder — How this works

This folder contains **15 review briefs** (`Day1.md` … `Day15.md`) and a **`reports/`** subfolder where the review session writes its findings (`DayN-report.md`).

## What a Review session is (and isn't)

- A **Review session** is a **fresh Claude Code session** whose only job is to **audit the day's build against the acceptance criteria** and write a report. Hand it `docs/Review/DayN.md`.
- The reviewer is an **independent skeptic**, not the builder. It assumes nothing works until proven. It **runs commands, opens files, hits endpoints** to gather evidence.
- **The reviewer does NOT modify code.** It only inspects and reports. Fixes happen in a subsequent Build session (which you hand both `docs/Build/DayN.md` and the report).

## Verdicts (used per checklist item)

- ✅ **Done** — meets the criterion; evidence attached.
- 🟡 **Partial** — mostly there; specific gap noted.
- ❌ **Not built** — missing or non-functional.
- 🔵 **Can be improved** — works, but quality/clarity/robustness concern worth a fix (non-blocking).

## Output

The reviewer writes **`docs/Review/reports/DayN-report.md`** using the template embedded in each review brief. Every report ends with:
- an **overall verdict** (SHIP / FIX-THEN-SHIP / NOT-READY), and
- a **prioritized punch-list** (P0 blockers → P1 → P2 improvements) that a Build session can consume directly.

## The loop
Build → Review (writes report) → Build (fixes P0/P1 from report) → Review again → … until the founder (Appu) is satisfied and the manual test passes. Then the day is closed.
