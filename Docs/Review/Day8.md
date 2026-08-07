# Review — Day 8: Memory Write & Consolidation

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day8-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day8.md` + PRODUCT.md §11/§5.4/§13.8. Verdicts with evidence.

## 1. Method
Play a session with distinctive moments (name the companion, a memorable choice), end it, and inspect what got written. Confirm consolidation is **async** (session‑end returns immediately; a Celery task runs). Count raw events vs. stored memories (salience filtering). Run a vector NN query. Confirm cascade delete removes memories.

```bash
cd backend && pytest apps/memory -q
python manage.py shell -c "from apps.memory.models import Memory; print(Memory.objects.count())"
# inspect STM keys in Redis during a session; confirm they exist then expire
# vector NN sanity: query a seeded memory's neighbors
# delete the child; assert Memory rows for that child are gone
```

## 2. Checklist (1:1 with Day 8 acceptance criteria)
1. STM captures choices/reactions/retries/dwell in Redis (evidence).
2. Consolidation runs **async** on `end` (session‑end not delayed; task on a worker).
3. Salient LTM written with **embeddings**; mundane NOT (few meaningful memories, not a transcript).
4. Child‑initiated moments captured as high‑salience (name/choice present).
5. pgvector ANN index exists; NN query returns sensible neighbors.
6. Memory deletable via child cascade; encrypted at rest; not plaintext‑logged.
7. lint/test/CI green incl. salience‑filter test (N events → k ≪ N).

## 3. Cross-cutting checks
- **Selectivity (§11.4):** the core risk is storing everything. Verify the salience threshold actually drops the mundane — a transcript dump is **P0**.
- **Privacy (§13.8):** child memory is the most sensitive data — confirm encryption at rest, no third‑party training, cascade delete, access control, no plaintext logs.
- **Async correctness:** session‑end returns immediately; consolidation is a Celery task, not inline (inline → P0 for child‑wait).
- **Scope discipline:** no retrieval/day‑two/learning front‑run.

## 4. Report to write → `docs/Review/reports/Day8-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 8 Review Report — Memory Write & Consolidation". Include the raw‑events‑vs‑memories count as evidence for selectivity.

## 5. Verdict rules
**NOT‑READY** if any P0: no salience filtering / transcript dumping, consolidation blocks the child, memory not deletable, privacy gap, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 7 ✅.

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 9.
