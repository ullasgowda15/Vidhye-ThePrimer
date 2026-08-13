# Build — Vertex embeddings (drop OpenAI; make it genuinely all-Google)

> **Hand to a fresh Claude Code session (repo root), after the Vertex Gemini provider build (`36f1147`/`d50a824`) is on `main`.** Goal: move embeddings from OpenAI to **Vertex AI**, so the entire child-data stack (beats, safety, memory, image moderation, **and now embeddings**) runs on **one Google service account, one credential, the $300 credit — no OpenAI vendor at all.**
>
> **Source of truth:** `docs/PRODUCT.md` §11.3/§16.6 (embeddings), §13.8 (no training on child data); `docs/Hosting.md §3`. Reuse the Vertex client the Gemini build created (`apps/common/vertex.py`) — do **not** build a second auth path.

---

## What was verified before this brief (so nothing surprises us later)

Confirmed by reading the code:

- `backend/apps/media/adapters/embedding.py` has `OpenAIEmbeddingProvider` (name `openai`, implemented), `LocalHashEmbeddingProvider` (name `local`, lexical fallback), and **`GoogleEmbeddingProvider(_UnimplementedEmbeddingProvider)`** (name `google`) — a stub that raises. **← build target.**
- `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL=text-embedding-3-small`, **`EMBEDDING_DIMENSIONS=1536`** in `settings/base.py`. The `memory.embedding` pgvector column is that width; `adapters.embedding.embed` **rejects a provider whose output width ≠ `EMBEDDING_DIMENSIONS`**. Changing the width is a migration + (if data exists) a re-embed.
- `model_ids.CHILD_DATA_MODEL_SETTINGS` already includes `EMBEDDING_MODEL`, so the `:free`/training guard already covers embeddings — a bare Vertex model id passes (no `:free`).
- **There are no memory rows yet** (fresh pilot), so a dimension change needs a migration but **no re-embed**.

---

## The one decision that avoids a migration (do this)

Use **`gemini-embedding-001`** with **`output_dimensionality=1536`**. That keeps the vector width at the current **1536**, so the `memory.embedding` column and its HNSW index are unchanged — **no migration, no index rebuild.** `EMBEDDING_DIMENSIONS` stays `1536`.

Two rules that come with truncated Gemini embeddings:
- **L2-normalize** each returned vector before storing it. Google's guidance: `gemini-embedding-001` outputs below its native 3072 are **not** pre-normalized, and our retrieval uses cosine distance — an unnormalized vector silently corrupts ranking. Normalize in the provider.
- Set `task_type` appropriately (`RETRIEVAL_DOCUMENT` when embedding a memory to store, `RETRIEVAL_QUERY` when embedding the retrieval context) if the adapter distinguishes them; if it only has one `embed(text)`, use `RETRIEVAL_DOCUMENT` and note it.

*(If for any reason `gemini-embedding-001` at 1536 is unavailable, fall back to `text-embedding-005` at 768 — but then you MUST add a migration altering the column to 768 and rebuilding the HNSW index, and set `EMBEDDING_DIMENSIONS=768`. Prefer the no-migration path.)*

---

## Scope

### 1. Implement `GoogleEmbeddingProvider` — `backend/apps/media/adapters/embedding.py`
Replace the stub with a real provider that satisfies the existing embedding protocol (`name`, `embed(text) -> list[float]`, whatever `is_configured`/dimension surface the others expose). Requirements:
- **Reuse `apps/common/vertex.py`** for the client (`vertexai=True`, service-account creds) — the same credential as the LLM/moderation path. No API key, no second auth.
- Model from `EMBEDDING_MODEL` (`gemini-embedding-001`), `output_dimensionality` from `EMBEDDING_DIMENSIONS` (1536), **L2-normalized** output, correct `task_type`.
- Output length **must equal** `EMBEDDING_DIMENSIONS` — let the existing `embed()` width guard verify it; add a provider-level assertion too so a config drift fails loudly, not silently.
- `name = "google"`. `is_configured()` true when the Vertex creds/project are present (mirror the LLM provider), so a mis-set deployment degrades to `local-hash` rather than crashing consolidation.

### 2. Config — `settings/base.py` + `.env.example`
`EMBEDDING_PROVIDER=google`, `EMBEDDING_MODEL=gemini-embedding-001`, `EMBEDDING_DIMENSIONS=1536` (unchanged). No `OPENAI_API_KEY` needed anywhere for embeddings. Keep `OpenAIEmbeddingProvider` and `local-hash` as seams — this is additive.

### 3. Region availability (the Flash-Lite lesson, applied)
The Gemini build found `asia-south1` silently 404s on Flash-Lite. **Confirm `gemini-embedding-001` is actually served at `GOOGLE_CLOUD_LOCATION=global`** (or whatever the deployment uses) with a real `embed_content` call, not just a `models.get()`. If it isn't at `global`, document the region that serves both the LLM models and the embedding model, or note the split.

### 4. Cost (light) — `apps/analytics/pricing.py`
If the embedding call goes through the cost tracer, add a `gemini-embedding-001` rate so it's priced, not `priced=False`. If embeddings aren't cost-traced today, leave it and note that in the report.

### 5. Docs — make the plan all-Google
Update `docs/Hosting.md §2/§3/§4`, `docs/Test/test-prerequisites.md`, and `docs/Runbooks/Deploy-GCP.md §7`: embeddings now run on Vertex `gemini-embedding-001` under the same service account; **remove `OPENAI_API_KEY` / OpenAI from the required list** (the app no longer needs any OpenAI account). The `§13.8` story gets simpler: one vendor for all four child-data routes.

---

## Acceptance criteria (done when ALL true)
1. With Vertex creds set and **no `OPENAI_API_KEY`**, a real memory embeds via `gemini-embedding-001` and `select`/`embed` returns a **1536-dim, L2-normalized** vector; `EMBEDDING_DIMENSIONS` unchanged; **no new migration on `memory.embedding`**.
2. Semantic retrieval works end-to-end: seed two memories, retrieve by a semantically-related (not lexically-identical) context, confirm the right one ranks first — the thing `local-hash` could not do.
3. The `embed()` width guard passes (output == 1536); a deliberately wrong `output_dimensionality` fails loudly.
4. `gemini-embedding-001` confirmed served at the deployment's `GOOGLE_CLOUD_LOCATION` with a live call.
5. `EMBEDDING_PROVIDER=google` needs **no OpenAI account**; `launch_check.privacy.provider_terms` still `OK` (bare Vertex id, no `:free`).
6. `make lint && make test && make evals` green; `mypy` clean; `pip-audit` clean (no new dep, or the Google SDK already added by the Gemini build); docs updated; `OPENAI_API_KEY` gone from the required config.

## Do NOT
- ❌ Don't build a second Vertex auth path — reuse `apps/common/vertex.py`.
- ❌ Don't change `EMBEDDING_DIMENSIONS` off 1536 unless you're forced onto `text-embedding-005`, and if so, include the column migration + index rebuild and say so loudly.
- ❌ Don't remove `OpenAIEmbeddingProvider` / `local-hash` — keep the seams.
- ❌ Don't touch anything the Day-1–15 audit proved clean, or the Vertex Gemini provider just built — this is additive.

## End state
Every child-data route — beats, safety, memory, image moderation, **embeddings** — runs on one Vertex service account under the $300 credit, data-excluded; recall is semantic again with **no OpenAI vendor**; no migration; docs updated. Commit with the real CI run id. Reviewed together with the Gemini provider under `docs/Review/Vertex-Gemini.md`.
