# Day 8 Review Report — Memory Write & Consolidation
**Reviewed:** 2026-08-11 · **Commits:** `52ccaeb` ("Day 8: memory write + consolidation"), `a134521`, tree at `459a561` ("Day 8 fix: one moment per moment") · **Reviewer:** Claude Code (review session) · **Method:** empirical — I read the child's real memories out of the founder's own Postgres, re-ran consolidation on a real sitting inside a rolled-back transaction to capture the candidates-vs-kept line, and inspected live Redis. No code was modified.

## Overall verdict

**FIX-THEN-SHIP.** The day's central risk was storing everything, and this build does not. Selectivity is real and measurable: a seven-beat sitting produced **6 candidates → 3 memories**, with the best rejected candidate scoring 0.44 against a 0.50 threshold. The four properties the brief cares about — selective, typed, embedded, private — all hold under inspection rather than only in the tests. Consolidation is genuinely off the child's path (`transaction.on_commit` → `.delay`), content is Fernet ciphertext on disk, the HNSW cosine index exists in the live database, and deleting a child takes their memories with them.

No P0. Three things to fix, one of which will otherwise be inherited by Day 9's retrieval as silent wrongness:

1. **P1 — entity keys are never normalised**, so the same companion is already stored under two graph keys (`companion:ruby` ×4 and `companion:Ruby` ×1) in the founder's live data. §11.7's interest and relationship graphs are built by querying this column, and Day 9 retrieval keys off it. This is cheap now and a backfill later.
2. **P2 — the vector is computed over text the dedupe logic deliberately ignores.** `_signature` strips the beat anchor because the anchor is scenery; `_write` embeds the whole sentence including it. The retrieval handle therefore encodes the scenery that the same module already decided is not what the memory is about.
3. **P2 — CI has never seen Day 8.** All three commits are local; `origin/main` is still at `94608dc`. Criterion 7 is half-verified by construction.

The `event`-type rows also still read like a route log in *voice* if not in volume (`Spidey chose "Count the spiders" — Good morning, Spidey!`), which matters because Day 12 shows this column to a parent. Noted as P3 rather than a defect.

## Scorecard

| # | Criterion | Verdict | Evidence (cmd/file → result) |
|---|-----------|---------|------------------------------|
| 1 | STM in Redis captures choices/reactions/retries/dwell | ✅ | Live keys on the founder's Redis: `primer:stm:<session-uuid>`, `type` → **list**, `ttl` → **21118s** of a 6h `MEMORY_STM_TTL_S` (`dbsize` 120). `lrange` shows the real shape: `{"event":"beat",…,"anchor":…,"emotion":…,"objective":…,"refs":["companion:pip"],"safety":"passed"}` and `{"event":"choice",…,"dwell_ms":2400,"retries":0,"assisted":false}` — all four required signals present. Mirrored on the row: `PlaySession.stm_digest` → `{'counts': {'beat': 7, 'choice': 6, 'retries': 2, 'assisted': 1, 'dwell_ms': 19600, 'session_end': 1}, 'highlights': ['chose: Go toward the flowers', …]}`. Every failure path is swallowed (`stm.py` docstring, and it holds — nothing in the module raises). |
| 2 | `consolidate` runs **async** on `end`; session-end not delayed | ✅ | `orchestrator.py:1577` — `transaction.on_commit(lambda: _consolidate_session(locked, reason))` → `:1611` `consolidate.delay(str(session.pk))`. Both halves matter: `.delay` keeps it off the request, `on_commit` keeps a rolled-back close from being remembered. Tests pin both: `test_session_end_enqueues_and_does_not_consolidate:42`, `test_a_rolled_back_close_enqueues_nothing:69`. Idempotent against Celery's at-least-once via `PlaySession.consolidated_at` (`test_consolidating_twice_does_not_double_a_childs_memories:315`). No autoretry, with the reason written down in `tasks.py`. |
| 3 | Salient memories written **typed** + **embedded**; the mundane is not | ✅ | **The headline number, from re-running consolidation on a real sitting** (rolled back): `memory.consolidate session=78d2a8c9 candidates=6 kept=3 duplicate=0 dropped=3 embedded=3 types=event,preference,skill_moment top_dropped=0.44 threshold=0.50`. That sitting held **14 STM events** (7 beats, 6 choices, 1 close) and **13 persisted rows** → **3 memories**. Corpus-wide: 3 consolidated sittings, **17 beats + 14 choices → 10 memories**; types `skill_moment` 4, `event` 3, `preference` 2, `entity` 1; **embeddings present on 10/10**. Bounded twice over — `MEMORY_SALIENCE_THRESHOLD=0.5` and `MEMORY_MAX_PER_SESSION=6`. `memory.branch_picks_folded picks=5 kept_seq=5` shows `459a561`'s folding doing its job. Test: `test_a_session_of_taps_does_not_become_a_row_per_tap:126`. |
| 4 | Child-initiated moments captured as high-salience `entity`/`event` | ✅ | `child_initiated` is the heaviest of the five §11.4 factors at **0.34** (`salience.py:57`) and is the only one that can carry a candidate over the line alone (`test_meeting_a_character_can_clear_the_threshold_on_its_own:150`). Live rows bear it out: a `preference` memory reading `Spidey chose to look under the slide.` and an `entity` row, and the digest's `highlights` are exclusively the child's own picks. `test_a_character_the_child_named_becomes_a_high_salience_entity:247` covers the named-dragon case the brief singles out. |
| 5 | pgvector ANN index exists; NN query returns sensible neighbours | 🟡 | Index is live, not just declared: `pg_indexes` → `CREATE INDEX memory_embedding_hnsw ON public.memory_memory USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')`. HNSW over IVFFlat is argued in `models.py:160` and the argument is right — IVFFlat cannot be trained on the empty table this ships as. A `CosineDistance` NN query runs and returns ordered neighbours. **Why not ✅:** `EMBEDDING_PROVIDER=local` here (no `OPENAI_API_KEY`), and `LocalHashEmbeddingProvider` is a hashing-trick embedder that documents itself as "not a semantic model and never pretends to be". So *ordering* is verified and *neighbour quality* is not — the nearest neighbour of a counting memory was an unrelated choice event at `d=0.2568`, while the genuinely related counting memory sat at `d=0.6151`. See P2-1: some of that gap is the anchor, not the provider. |
| 6 | Deletable via `child` cascade; encrypted at rest; not plaintext-logged | ✅ | **Ciphertext on disk:** raw SQL against the column returns `'gAAAAABqef4jPEKHW5q65B1QOGTbyYqlp-QD…'` (Fernet), decrypting to readable content only through the ORM (`test_what_consolidation_writes_is_ciphertext_on_disk:179`). **Cascade:** deleting the child took `memory_memory` from **10 → 0** in one statement (rolled back; table restored to 10). **Logs:** no `logger.*` call in `apps/memory` carries `content`; the job's line is counts, types and scores only; `Memory.__str__` deliberately omits content because it lands in admin change messages and tracebacks. **STM hygiene:** all three consolidated sessions have **no** surviving Redis key — the working copy is discarded, not left to age out. Admin is read-only (`test_admin.py`). |
| 7 | `make lint && make test` + **CI green**, incl. a salience-filter test | 🟡 | Local is clean: `ruff check backend` → **All checks passed!**; `mypy .` → **Success: no issues found in 185 source files**; `pytest` → **exit 0** across **712 collected** tests (**89** in `apps/memory`). The salience-filter test the criterion names exists and is the right shape (`test_a_session_of_taps_does_not_become_a_row_per_tap`, plus `test_the_mundane_is_not_remembered:209` and `test_every_memory_cleared_the_threshold:202`). **Why not ✅:** **CI has not run on this work at all.** `52ccaeb`, `a134521` and `459a561` are contained in no remote branch and `origin/main` is still `94608dc` (Day 7 fix). "CI green" is unevidenced until it is pushed. |

Counts: ✅ 5 · 🟡 2 · ❌ 0

## Findings

### P1-1 — Entity keys are never normalised, and the graph is already forked
**Where:** `apps/memory/candidates.py:630` (`_entities_for` passes `beat.continuity_refs` through verbatim) · `apps/memory/consolidation.py:169-174` (model proposals are truncated to 120 chars and otherwise trusted) · `apps/memory/prompts.py:102` (the field description *suggests* `['character:sparkle', …]` by example; nothing enforces it).

**Evidence — the founder's live data, not a hypothetical:**

```
4x companion:ruby        1x companion:Ruby       ← the same companion, two keys
2x character:elephant    1x interest:Elephant    ← the same elephant, two namespaces
5x objective:count_to_3  1x child:spidey  1x place:slide  1x topic:count
```

The rules half emits `companion:ruby` from `continuity_refs`; the model half emitted `companion:Ruby` because a model asked for "namespaced keys" returns the display name it was shown. Nothing between the proposal and the row lowercases anything.

**Why it matters now rather than later:** `entities` is not decoration. §11.7's interest graph and the relationship graph are built by *querying this column*, and Day 9's retrieval rules read it. A child whose companion is stored under two keys has their favourite split across two nodes — each with half the recurrence count, which also feeds the `repetition` salience factor and so quietly suppresses the score of the thing that matters most. It is invisible in every test because the tests construct their own entities consistently.

**Suggested fix (Day 9, before retrieval is built on it):** normalise on write — casefold the value half, validate the namespace against a closed set, drop or log what does not match — in one place both halves pass through. Then a one-off backfill; there are 10 rows today and it will never be cheaper.

### P2-1 — The embedding is computed over text the dedupe logic deliberately strips
**Where:** `apps/memory/consolidation.py:235` — `embedding_adapter.embed(candidate.content, provider)`.

`459a561` established that a memory's `content` is `<what the child did> — <the beat's first clause>` and that **the anchor is scenery**: `salience._signature` partitions on `GIST_SEPARATOR` and compares only the gist, because comparing whole sentences made one repeated tap look like two memories and made two different memories from one beat look like duplicates. That reasoning is sound and well documented.

The vector never got the memo. `_write` embeds the entire string, anchor included, so Day 9's retrieval handle encodes the scene the module already decided the memory is not about. Observed: the nearest neighbour of `Spidey counted 3 all by themselves — Ruby rolls up beside you in the warm sand…` is `Spidey chose "Follow more tracks" — Ruby rolls up beside you in…` at **d=0.2568** — a different act, retrieved as near-identical because they share scenery — while the actually-related `Spidey counted the elephant's trunk waves.` sits at **d=0.6151**.

**Honest caveat, because it changes how much weight this deserves:** these distances come from `local-hash`, which is lexical by construction, so shared literal text dominates it far more than it would a real semantic model. This is *not* proof that a real embedding would rank the same way. What it does show is a structural inconsistency — two comparisons in the same pipeline reading two different strings — and Day 9 is about to build ranking on the one that was never reconsidered. Worth a decision, not a panic: embed the gist, embed both, or write down why the whole sentence is right.

### P2-2 — CI has never run on Day 8
Criterion 7 requires CI green. `git branch -r --contains 52ccaeb` returns nothing; `origin/main` is `94608dc`. Local lint and tests are green and I verified them myself, but the Day 8 tree has never been through the pipeline — including the Postgres/pgvector service container that the HNSW migration actually exercises differently from a long-lived local database. Push and confirm before Day 9 builds on it.

### P3-1 — `event` memories still read like a route log, in voice if not in volume
`459a561` fixed the *count*. The register is still ours rather than a friend's:

> `Spidey chose "Count the spiders" — Good morning, Spidey!`
> `Spidey chose "Follow more tracks" — Ruby rolls up beside you in the warm sand, S…`

Three of ten rows are `chose "<button label>"` echoes. §11.3 asks for "how a friend would recall it later", and the model half writes exactly that when it runs (`Spidey counted the elephant's trunk waves.`) — it is the deterministic half that quotes UI labels. Not a correctness bug, and the salience filter is doing its job. It matters because Day 12 puts this column in front of a parent as the transparency view, and a parent reading their child's memories should not see button text in quotation marks.

## Cross-cutting checks the brief singles out

**Selectivity (§11.4) — the P0 risk, and it is closed.** Nothing resembling a transcript dump. Two independent bounds (threshold 0.5, cap 6), a near-duplicate filter, and greedy diversity that measures each candidate against what has already been kept. The strongest evidence is the re-run: on a sitting whose memories I had just deleted, consolidation wrote **3, not 4** — because the child's *other* sessions' memories now counted as prior history and the novelty filter suppressed the repeat. The filter works across sittings, not just within one.

**Privacy (§13.8).** Encrypted at rest (verified as ciphertext in the column, not asserted); key from `MEMORY_ENCRYPTION_KEY`, derived from `SECRET_KEY` in dev and **required** by `prod.py`, so rotating `SECRET_KEY` cannot orphan every memory; cascade delete verified live; no plaintext in logs, in `__str__`, or in the admin (read-only); STM discarded at consolidation rather than left to expire; narration deliberately not copied into Redis beyond a first-clause anchor. `deleted_at` exists unused, for the Day 12 reason its comment gives — a tombstone so consolidation cannot rediscover what a parent asked to forget. This is the most careful privacy work in the codebase so far.

**Async correctness.** No child-facing wait: `on_commit` + `.delay`, no inline path, and the whole job is written to degrade rather than fail — the model half unreachable costs the subtle memories (`test_the_model_half_can_fail_and_the_factual_memories_survive:370`), the embedding provider unreachable costs the vectors and not the rows (`test_an_embedding_outage_costs_the_vector_and_not_the_memory:388`, and `embedding` is nullable precisely for this).

**Scope discipline.** Clean. No retrieval, no day-two greeting, no learning engine. `last_recalled_at`, `recall_count` and `decay` are declared and untouched, which is the correct amount of Day 9 to have in the schema.

## What I could not verify
- **Neighbour quality against a real embedding provider** — no `OPENAI_API_KEY` in this environment; `local-hash` is a lexical stand-in. Structural correctness (index, opclass, distance function, width check) is verified; semantic ranking is not.
- **CI** — cannot be triggered from a review session, and the commits are unpushed (P2-2).
- **The 6h STM TTL actually firing** — observed at 21118s remaining and observed *absent* after consolidation, which is the path that matters; natural expiry was not waited out.

## Punch-list for the next build session
1. **P1-1** — normalise `entities` on write (casefold + closed namespace set), one place both halves pass through; backfill the 10 existing rows.
2. **P2-1** — decide what gets embedded, and write the reason down next to the decision.
3. **P2-2** — push Day 8 and confirm CI green before Day 9 lands on top of it.
4. **P3-1** — stop quoting button labels in `event` content; say what the child did.

## Five-line summary
1. **FIX-THEN-SHIP** — no P0; memory is selective, typed, embedded, encrypted and deletable, verified against the founder's live data rather than the build's own tests.
2. Selectivity is the day's win and it is real: **14 STM events → 6 candidates → 3 memories**, top rejected candidate 0.44 vs a 0.50 threshold; 17 beats + 14 choices across three sittings yielded **10** memories total.
3. Consolidation is properly off the child's path (`on_commit` + `.delay`), degrades in every direction, and is idempotent against Celery redelivery.
4. **P1:** entity keys are unnormalised and the graph is already forked (`companion:ruby` vs `companion:Ruby`) — fix before Day 9 retrieval keys off that column.
5. **P2:** the vector is computed over the beat anchor that the dedupe logic deliberately strips; and CI has never seen Day 8 because the three commits were never pushed.
