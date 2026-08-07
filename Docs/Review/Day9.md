# Review — Day 9: Memory Retrieval & the Day-Two Greeting

> **Session type:** REVIEW — this is the **moat feature**, hold the bar high. Independent auditor. Gather evidence, then write `docs/Review/reports/Day9-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day9.md` + PRODUCT.md §11.9/§4.5/§8.7/§19.4/§13.5. Verdicts with evidence.

## 1. Method
Seed **two children with different histories**; trigger returning greetings for each and confirm they reference the correct, specific memories **by name**. Inspect the retrieved bundle size (must be small/bounded). Plant an injection string inside a memory and confirm it doesn't alter behavior. Test the new‑child fallback. Explicitly test for cross‑child leakage.

```bash
cd backend && pytest apps/memory apps/story -q
# inspect a retrieval bundle: size, rule-includes (fears/companion state) present, token count within budget
# child A greeting vs child B greeting -> different, correct, no bleed
```

## 2. Checklist (1:1 with Day 9 acceptance criteria)
1. Retrieval bundle small/ranked/**token‑bounded**; rule‑includes (fears + companion state) always present.
2. Day‑two greeting references a **specific real memory by name** — correct for 2 distinct children (quote both).
3. Memory injected as DATA; planted injection‑through‑memory neutralized.
4. `last_recalled_at`/`recall_count` update on use.
5. New child (no memories) → graceful first‑meeting fallback (no empty/awkward greeting).
6. Retrieval returns only the **requesting child's** memories (no cross‑child leakage — prove with the two children).
7. Retrieval latency within beat budget; prompt bounded; lint/test/CI green incl. greeting‑references‑seeded‑memory test.

## 3. Cross-cutting checks
- **Correctness of recall:** does it reference the *right* child's memory? **Cross‑child bleed is P0** (privacy + trust).
- **Boundedness (§19.4):** it retrieves *few* high‑value memories, not everything — confirm the token budget is enforced.
- **Security regression:** the memory path is a new injection surface — confirm §13.5 holds through it (the bundle passes `guard_input`).
- **Scope discipline:** no learning/interest‑graph front‑run.

## 4. Report to write → `docs/Review/reports/Day9-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 9 Review Report — Memory Retrieval & Day‑Two Greeting". Quote both children's greetings as evidence for criterion 2.

## 5. Verdict rules
**NOT‑READY** if any P0: greeting doesn't reference a real memory, **cross‑child leakage**, injection‑through‑memory succeeds, unbounded retrieval, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 7 ✅ — this is the moat feature; hold the bar high.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 10.
