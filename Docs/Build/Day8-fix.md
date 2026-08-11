# Build — Day 8 FIX: normalise the memory graph before Day 9 keys off it

> **Session type:** BUILD (fix pass for Day 8). Read fully, fix in scope, run the **Self-review** in §6, then **Commit & push** in §7. Do **not** start Day 9.
>
> **Inputs:** this file · `docs/Build/Day8.md` · `docs/Review/reports/Day8-report.md` (line refs below are from it). **Source of truth:** PRODUCT.md §11.3 (memory voice), §11.7 (interest/relationship graphs), §19.4 (retrieval).
>
> **Why:** Day 8 reviewed **FIX-THEN-SHIP, no P0**. Memory is selective/typed/encrypted/deletable. But the `entities` column — which **Day 9 retrieval and §11.7's graphs query** — is already forked in the founder's live data, so this must land before Day 9 builds on it.

## 1. P1 — normalise entity keys (and backfill)
`_entities_for` passes `continuity_refs` verbatim (`candidates.py:630`) and the model half's proposals are trusted (`consolidation.py:169-174`), so the same companion is stored as **`companion:ruby` ×4 and `companion:Ruby` ×1**, and one elephant as `character:elephant` vs `interest:Elephant`. A forked key splits a child's favourite across two nodes, halves its recurrence count, and so quietly suppresses the `repetition` salience factor.
**Do:**
- Normalise every entity key in **one place both halves pass through**: **casefold the value half**, validate the **namespace against a closed set** (`companion|character|interest|objective|place|topic|child` — reconcile `character` vs `interest` and pick one rule), drop-or-log what doesn't match.
- **Backfill** the 10 existing rows (cheapest it will ever be) with a data migration or one-off command; merge the forked keys.
- Test: two proposals differing only in case produce **one** key; an unknown namespace is dropped/logged, not stored.

## 2. P2-1 — decide what gets embedded, and write the reason down
`_write` embeds the whole `content` string including the beat anchor (`consolidation.py:235`), but `salience._signature` deliberately **strips** that anchor as scenery — so the retrieval vector encodes exactly what the dedupe logic decided the memory is *not* about. **Do:** make the embedding consistent with the dedupe view — embed the **gist** (the part left after stripping the anchor), OR embed both deliberately — and **write the rationale in a comment** next to the call. This is a decision to make now because Day 9 ranks on this vector.

## 3. P3-1 — stop quoting button labels in `event` content
3 of 10 rows read like a route log — `Spidey chose "Count the spiders" — Good morning, Spidey!`. §11.3 asks for how a *friend* would recall it; the model half already does (`Spidey counted the elephant's trunk waves.`), it's the deterministic half quoting UI labels. Day 12 shows this column to a parent. **Do:** in the deterministic path, render what the child *did* in natural language, not the raw button label in quotes.

## 4. P2-2 — CI (founder + verify)
All three Day-8 commits are unpushed; `origin/main` is `94608dc`. **Push** so CI runs — the Postgres/pgvector service container exercises the HNSW migration differently than a long-lived local DB. Confirm green before Day 9.

## 5. Do NOT do
- ❌ No Day-9 retrieval / day-two greeting / learning engine. ❌ Don't touch `last_recalled_at`/`recall_count`/`decay` (Day 9's). ❌ Don't weaken selectivity, encryption, or the degrade-in-every-direction behaviour. ❌ Don't commit `.env`/`backend/media/`.

## 6. Self-review & evaluation (REQUIRED)
Write a short evaluation rating each **Verified/Partial/Unverified** with evidence:
1. `make lint && make test` + migration clean; CI green after push.
2. Entity keys normalised on **both** write paths; the 10 live rows backfilled and de-forked (show the before/after key counts).
3. Embedding text decision made and documented; state which you chose.
4. `event` content no longer quotes button labels (paste a before/after row).
5. What you could not verify (real-embedding neighbour quality stays a later-day item — no key here).

## 7. Commit & push
`git status` (no secrets/media) → stage fix + migration + `docs/Build/Day8-fix.md` → commit `Day 8 fix: normalise entity graph, embed the gist, humanise event memories` → `git push`, confirm Actions green.

## 8. End state
The entity graph is single-keyed and backfilled so Day 9 retrieval reads one node per thing; the retrieval vector matches the dedupe view (or the exception is documented); event memories read like a friend's recollection, not a route log; and Day 8 is on the remote with CI green. Ready for Day 9.
