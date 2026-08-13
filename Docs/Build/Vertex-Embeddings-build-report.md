# Build report — Vertex embeddings (drop OpenAI)

> Brief: `Docs/Build/Vertex-Embeddings.md`. Reviewed with the Gemini provider under `Docs/Review/Vertex-Gemini.md` §§9–11.
> Self-reviewed against those three sections; **not** independently reviewed.

## The short version

Embeddings were the last child-data route not on Vertex, and the only reason this product needed an OpenAI account. They now run on `gemini-embedding-001` at 1536 dimensions, on the **same service account** as beats, the safety grader, the memory digest and the image gate. No new dependency, **no migration**, and recall is genuinely semantic for the first time — proven live, against a control.

## Proven live

**Semantic recall — three queries sharing no content word with the memory each should find.** Through `memory.retrieval._nearest`, i.e. the real pgvector column, the real HNSW index and the real cosine ordering:

| query | wanted | Vertex ranking | `local-hash` |
|---|---|---|---|
| "the winged reptile he invented" | dragon | **dragon** > counting > fear | fear ✗ |
| "how the arithmetic with the amphibians went" | counting | **counting** > dragon > fear | fear ✗ |
| "that rumbling noise in the sky which frightened him" | fear | **fear** > dragon > counting | fear ✓ |

3/3 with the correct full ordering. The `local-hash` control gets 1/3, and degenerately — the *same* memory wins every query, which is what a lexical model does when the query shares no stem with anything. That control is the point: it shows the test is discriminating rather than easy.

**Normalization — measured, not assumed.** Embedding one sentence at three widths:

```
3072 dims -> L2 norm 1.000000   (native; already normalized)
1536 dims -> L2 norm 0.690528
 768 dims -> L2 norm 0.588767
```

So truncated output genuinely is not unit-length. Every stored vector in the live run came back `L2=1.000000` after the provider's normalization.

**Region.** `gemini-embedding-001` at 1536 answers a real `embed_content` call in `global`, `us-central1`, `asia-south1` and `asia-southeast1` — so unlike Flash-Lite it is not the constraint on region choice. `text-embedding-005` 400s at 1536 everywhere (it is a 768-native model), which is the fallback the brief named; it was not needed.

**No migration.** `manage.py makemigrations --check --dry-run` → *No changes detected*. `memory.embedding` is still `vector(1536)` with `memory_embedding_hnsw` over `vector_cosine_ops`.

**launch_check on the all-Google config, with no OpenAI key:**

```
OK  safety.grader_reachable   google is configured for gemini-2.5-flash-lite
OK  safety.grader_is_separate the grader (gemini-2.5-flash-lite) is a different model from the writer (gemini-2.5-flash)
OK  privacy.provider_terms    every child-data model is on a route whose terms exclude training
                              (gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.5-flash-lite, gemini-embedding-001)
OK  spend.pricing             the configured providers are in the price table
```

The one blocking row locally is `spend.breaker` (no budget cap set on a laptop) — pre-existing and unrelated.

## Acceptance criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Real memory embeds via `gemini-embedding-001`, 1536-dim, L2-normalized, no `OPENAI_API_KEY`, no new migration | ✅ **live** — three memories stored, all `dims=1536 L2=1.000000`; `makemigrations --check` clean |
| 2 | Semantic retrieval end-to-end: right memory ranks first on a lexically-different context | ✅ **live** — 3/3 with correct ordering, against a `local-hash` control that gets 1/3 |
| 3 | `embed()` width guard passes; a wrong `output_dimensionality` fails loudly | ✅ tested — and there are now **two** guards: `embed()` compares against the column, the provider compares against what it asked for, so "the model ignored us" and "somebody changed `EMBEDDING_DIMENSIONS`" read differently |
| 4 | Model confirmed served at the deployment's `GOOGLE_CLOUD_LOCATION` by a live call | ✅ **live** — `global`, plus three other regions |
| 5 | No OpenAI account needed; `privacy.provider_terms` still `OK` | ✅ **live** — see above |
| 6 | lint / test / evals green, mypy clean, pip-audit clean, docs updated, `OPENAI_API_KEY` gone from required config | ✅ — **1464** backend (14 new) + **183** frontend + **44 passed / 1 skipped** evals; ruff clean both sides; mypy 0 issues in 262 source files; `pip-audit` no known vulnerabilities (no new dependency — the Gemini build already added the SDK); `makemigrations --check` clean. **CI green, run `31729138215`** — all five jobs, counts matching the local run exactly |

## CI

Build commit `3841e38`, recorded run **`31729138215`** on `0ac4ec6` — Backend, Backend image, Dependencies, Evals and Frontend all green.

The build commit's own run (`31722071810`) was **red on the Dependencies job and nothing else**, for a reason that has nothing to do with embeddings: `npm audit` picked up an advisory published against `nanoid <3.3.18` (GHSA-2v37-7h3g-55p8) some time between the previous green run and this one. `nanoid` reaches this tree only as `postcss -> nanoid` under the Vite build, so it is dev-only and nothing in it is served to a browser — but `--audit-level=high` is the blocking bar and the advisory is a high, which is the gate behaving correctly rather than a gate to argue with.

`0ac4ec6` is `npm update nanoid`: one package, one patch version, a three-line lockfile diff. It is recorded as this build's run because it is the last commit in this work, the same convention used for the Gemini build's pair. Worth stating plainly so a later reader does not mistake the red run for an embeddings failure: on `3841e38` the other four jobs — including the full backend suite and the evals — were already green, and `pip-audit` had passed inside the failing job before `npm audit` ran.

## Two judgement calls

**The `task` hint, and the one call-site change it required.** `gemini-embedding-001` is asymmetric: a stored document and the query that should find it are embedded under different instructions. The adapter had a single `embed(text)`, so the brief allowed using `RETRIEVAL_DOCUMENT` for both and noting it. I added an optional keyword instead (`TASK_DOCUMENT` / `TASK_QUERY`, defaulted, ignored by the symmetric providers) and passed `TASK_QUERY` from `memory.retrieval`. That is one changed line in a file the audit proved clean, and it is worth the reviewer's eye — the reason to accept it is that the failure it prevents is invisible: a correct index, searched slightly wrongly, forever, with nothing failing and no log line.

**Normalization is right for a reason that is *not* the obvious one.** Retrieval ranks with `CosineDistance` under `vector_cosine_ops`, and cosine is magnitude-invariant — so unnormalized vectors would rank **identically** today. The code and its comments say so, rather than repeating the tempting "unnormalized silently corrupts ranking", which is false here. What normalization actually buys: the column stays a set of unit vectors, so an L2 or inner-product index or query can ever be put over it; these vectors stay comparable in magnitude with `local-hash`'s (a database holding both should not hold two scales); and a stored vector means the same thing if `EMBEDDING_DIMENSIONS` ever changes. It costs one pass over 1536 floats, so it is cheap insurance — but it is insurance, not a bug fix.

## Not done, deliberately

**Embeddings are still not cost-traced.** `cost.record` is written at the two seams that spend money on a child's turn — `ModelRouter.generate_structured` and `media.service._get_or_generate` — and the embedding call goes through neither. So a consolidation run's embedding spend is invisible in `cost/session`. The brief allowed leaving it; it is worth knowing that the number is therefore a small under-count. At `gemini-embedding-001`'s $0.15/1M input tokens and a handful of one-sentence memories per sitting, the missing term is a fraction of a cent per session against a beat's $0.003–0.004 — real, but not the reason cost/session is what it is. Adding it means opening a cost trace in `memory.consolidation`, which is a change to a Day-8 path this build had no reason to touch.

## The config sweep, and two things it found

The brief named four documents. Left there, the repository would have gone on saying OpenAI in a dozen other places, so the sweep was widened to every tracked file. Three categories, treated differently:

- **Updated** — anything a person would act on: `CLAUDE.md` (auto-loaded into every future session, and it described the stack as "Anthropic Claude primary with an OpenAI/Google fallback router"), `README.md`, `Docs/Build/README.md`, `Docs/PROGRESS.md`'s *Config / keys* section (which still described OpenRouter, `nvidia/nemotron-…` and `local-hash` embeddings), the `OpenAIEmbeddingProvider` docstring (it claimed `OPENAI_API_KEY` was "load-bearing" and "used here and nowhere else" — true when written, false now), `test_launch_check.py`'s shipping-config fixture, and one row of `PRODUCT.md`'s stack table.
- **Left alone — historical record.** Every `Docs/Review/reports/Day*-report.md`, `Docs/Build/Day*.md`, and the Day 1–15 audit reports say OpenAI because that is what was true when they were written. Rewriting them would make the audit trail a lie, which is a worse problem than an inconsistency.
- **Left alone — still correct.** `openai==1.59.6` in `requirements.txt` (the OpenRouter path *is* the OpenAI SDK), the `OpenAIProvider` and `OpenAIEmbeddingProvider` seams, and `.env.example`'s "applies to OpenAI-compatible providers".

**`PRODUCT.md` got one row and no more.** It is the architecture source of truth and a build session does not get to rewrite it. The row now names the router's implemented providers *and* what the router is actually pointed at, with a pointer to `Hosting.md §4`; the architecture claim — a provider-agnostic router — is unchanged and still true.

### Two real problems found in the founder's `.env`

Consolidating four env files down to two (`.env` live and gitignored, `.env.example` the documented template) surfaced both:

1. **`SECRET_KEY` was defined twice, with different values** — a 9-character one and a 64-character one. `django-environ` takes the last, so the good one was winning *by ordering*. That is a trap rather than a bug: reorder the file and the product silently drops to a 9-character JWT signing key, which is what `.env.example` warns about in as many words ("a guessable key is a forgeable session"). Deduplicated, keeping the effective value byte-identical — verified by fingerprinting all 38 settings before and after, with the effective-value diff empty.
2. **The file still said `AI_PROVIDER_ORDER=openrouter,anthropic`** with `nvidia/nemotron-…` and `google/gemma-…` model ids, i.e. the pre-Vertex configuration, while every document said Google. It now matches: `google`, the Gemini ids, `IMAGE_MODERATION_PROVIDER=google` made explicit rather than left to a default, and the whole stack resolves from `.env` alone (`router -> google:gemini-2.5-flash`, `embed -> google gemini-embedding-001 1536`, `moderator -> GeminiImageModerator`).

### And one the suite caught on the way out

Setting `IMAGE_MODERATION_PROVIDER=google` in `.env` broke `test_an_anthropic_deployment_is_unchanged` — a test written *in the previous build* to prove that adding Gemini moved nothing for an Anthropic-only deployment. It asserts that `auto` picks Claude, and it was reading the provider from the developer's `.env` rather than from the code default, so it inherited `google`. **It would have passed in CI, where there is no `.env`, and failed only on the founder's laptop** — the same machine-dependence `tests/test_settings_validation.py` documents, and the reason the `.env.example` assertion in this build reads the file instead of the setting.

Fixed systemically in `conftest.py`'s `no_live_providers`, which now pins `IMAGE_MODERATION_PROVIDER` to the code default alongside the credentials it already blanks. Worth recording because the failure is the suite doing its job: it is the same class of drift that produced `AI_PROVIDER_ORDER=gemini` in three documents, caught this time by a test rather than by reading.

`OPENROUTER_API_KEY` is still populated and now unused. Left in place deliberately — deleting somebody's credential is their call, and the seam still works — but it is worth revoking if OpenRouter is not coming back.

## One thing found on the way, for the founder to decide

**The privacy disclosure does not mention the embedding provider.** `apps/accounts/privacy.py`'s "Who else sees it" names an AI provider, an image provider and a voice provider, and says "the words needed for that beat go to those companies". A memory's sentence goes to an embedding provider at *consolidation*, which is not a beat — so that route is not described. This predates today's work and is not made worse by it (the vendor count went **down**, and the routes carrying a child's words are now one company). It is left alone on purpose: the disclosure is parent-facing, §13.7 treats a materially changed disclosure as a new consent, and bumping `REQUIRED_CONSENT_TYPE` forces every parent to re-consent. That is the founder's call, not a build session's.
