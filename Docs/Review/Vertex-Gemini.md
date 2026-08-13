# Review — Vertex Gemini provider + Vertex embeddings (all child-data routes under Google Cloud)

> **Hand to a fresh Claude Code session (repo root), after BOTH builds are committed: `docs/Build/Vertex-Gemini.md` (the LLM provider + image moderation) AND `docs/Build/Vertex-Embeddings.md` (embeddings moved off OpenAI).** You are an independent reviewer — you did NOT write either build. This change puts **every child-data route (beats, safety grader, memory digest, image moderation, embeddings) on Vertex under one service account, a new SDK, and touches the safety image gate** — so the bar is "proven with a real Vertex call or an honest note that it wasn't", not "the tests pass".
>
> **One review, both builds.** §§1–8 below cover the Gemini provider + image gate; **§§9–11 cover the embeddings move.** Write a single report.
>
> **Source of truth:** `docs/PRODUCT.md` §16.1/§19.3, §13.3, §13.8, §16.6, §11.3; `docs/Hosting.md §3`; both build briefs.

## How to run it
Read both build briefs and the diffs. Verify against the acceptance criteria of **both** with real commands (`make lint`, `make test`, `make evals`, `pip-audit`, `mypy`, `launch_check`). Where a real Vertex call needs credentials you don't have, say so explicitly and verify everything around it (schema sanitizing, error translation, config gating, embedding dimension/normalization) with a faked client. **Write `docs/Review/reports/Vertex-Gemini-report.md`** with a P0→P1 punch-list covering both builds. Do not fix during the audit; if the founder authorizes a combined session, fix P0→P1 after the report and re-verify.

## The things most likely to be wrong — press hardest here

1. **Gemini schema rejection (highest risk).** Gemini's `response_schema` is an OpenAPI-3 subset. Confirm the **real beat schema** (not a toy) round-trips through the sanitizer and Gemini accepts it, and that the sanitizer changed the *wire schema only*, not the `Beat` contract the rest of the app validates against. A beat that Gemini 400s on is every story dead. If no live key: assert the sanitizer's output contains no `additionalProperties`/`$ref`/`$defs`/unsupported formats, and that a sanitized-then-parsed sample still validates as a `Beat`.

2. **Safety-block handling (safety + availability).** Gemini has its own content filters. Confirm: (a) safety settings are set permissive enough that our §13 grader stays the authority (Gemini isn't silently refusing benign kids' content); (b) a real block / empty candidate becomes `ProviderError` → the Day-5 fallback beat, **never** a dead stream or an unhandled exception. Drive a stubbed "blocked" response through `create_beat` and confirm the child gets a warm fallback.

3. **The image gate actually runs under Google (the F-1/F-2 recall layer).** With Google creds and **no `ANTHROPIC_API_KEY`**: confirm `IMAGE_MODERATION=auto` calls `GeminiImageModerator`, not the structural fallback. Confirm a provider failure is **fail-closed** (becomes a refusal via `media/safety.py`'s total wrapper, not an exception a child sees). Confirm `prod.py` still refuses `IMAGE_MODERATION=off`. This is the mitigation the audit said was unproven — prove it now.

4. **Cost metering.** `self.last_usage` is set from `usage_metadata` and read **before** any post-call raise (a refusal is still priced — Day-14 P1-5). Spans carry real token counts; if pricing rates were added, cost/session is a real number, else `priced=False` honestly.

5. **§13.8 still holds and is enforced.** `launch_check.privacy.provider_terms` = `OK` for the bare Gemini ids; `training_error` returns `None` for them and still fires for a `:free` route; `grader_is_separate` holds. Confirm Vertex's data-exclusion is *stated* (terms/purchasing fact), not merely assumed by the `:free` check — the guard only catches the cheap mistake.

6. **Auth & boot gating.** `prod.py` refuses to boot when `google` is selected but project/location/service-account creds are missing (plain-ASCII message). `is_configured()` returns false (not raises) on missing creds so `select_provider` falls through cleanly. No service-account JSON path or key is committed or logged.

7. **Dependency hygiene.** `google-genai` pinned; `pip-audit` clean; the CI `Dependencies` job green; `make test`/`make evals` still green after the new dep.

8. **Nothing the Day-1–15 audit proved clean regressed.** Spot-check cross-account isolation, deletion completeness, the ship gate, and the F-1/F-2/F-3 fixes are untouched. `AnthropicProvider`, `OpenRouterProvider`, `ClaudeImageModerator` seams still present.

## Embeddings move (Vertex, off OpenAI) — press hardest here too

9. **Dimension & no-migration claim (highest embeddings risk).** The build should use `gemini-embedding-001` at `output_dimensionality=1536` to keep the `memory.embedding` column at 1536 with **no migration**. Verify: `EMBEDDING_DIMENSIONS` is still 1536; there is **no new migration** altering `memory.embedding` (or, if the build was forced onto `text-embedding-005`/768, that the column migration + HNSW index rebuild are present and correct); and the `embed()` width guard passes (output == `EMBEDDING_DIMENSIONS`). A width mismatch that slips the guard corrupts every vector silently.

10. **Normalization & semantic recall actually works.** Truncated `gemini-embedding-001` outputs are not pre-normalized; confirm the provider **L2-normalizes** before storing, since retrieval uses cosine distance. Then prove recall is genuinely semantic: seed two memories, retrieve by a semantically-related but lexically-different context, and confirm the right one ranks first (the thing `local-hash` could not do). If no live key, verify the normalization code path and the dimension, and say the semantic-quality check is owed to the founder's real-key test.

11. **All-Google, no OpenAI, region-served.** With **no `OPENAI_API_KEY`** set, embeddings run via Vertex and `launch_check.privacy.provider_terms` is still `OK` (bare Vertex id, no `:free`); the reuse of `apps/common/vertex.py` (not a second auth path) is confirmed; `gemini-embedding-001` is confirmed served at the deployment's `GOOGLE_CLOUD_LOCATION` with a **live `embed_content` call** (the Flash-Lite/`asia-south1` 404 lesson applies to embeddings too); `OPENAI_API_KEY` is gone from the required config in code and docs; `OpenAIEmbeddingProvider`/`local-hash` seams remain.

## Automatic P0
- A child can get a **dead stream / raw error** from a Gemini refusal or block (not degraded to fallback).
- The **image gate silently doesn't run** under the intended Google config (structural fallback where vision was expected).
- Any **child-data model reaching a training-permitted route** (LLM **or** embedding), or the boot/preflight guards not catching it.
- Structured beats that **Gemini rejects** in the real schema (product-wide outage).
- **Embedding width mismatch** that passes the guard, or unnormalized vectors silently degrading recall.
- A **secret** (service-account JSON, key) committed or logged.

## Deliverable
`docs/Review/reports/Vertex-Gemini-report.md` (one report, both builds): verdict (SHIP / FIX-THEN-SHIP / NO-GO), findings table (id/sev/file:line/impact), per-finding proof, what you verified clean, and — stated plainly — **what could not be verified without a live Vertex credential** and exactly which acceptance criteria (of both builds) that leaves open for the founder's real-key manual test. End with the honest bottom line: is this safe to run for a child on Vertex, and what must change first.
