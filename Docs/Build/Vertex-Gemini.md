# Build — Vertex Gemini provider (all LLM + image moderation under Google Cloud)

> **Hand to a fresh Claude Code session (repo root).** Goal: run every child-data model on **Vertex AI Gemini**, billed to the founder's **$300 GCP credit** (data-excluded, no AI-Studio prepay, no OpenRouter). This is a real feature build, self-reviewed, then handed to `docs/Review/Vertex-Gemini.md`.
>
> **Source of truth:** `docs/PRODUCT.md` §16.1/§19.3 (provider seam), §13.3 (independent grader), §13.8 (no training on child data), §16.6 (media). `docs/Hosting.md §3` (why all four routes are paid/data-excluded).
>
> **Why now:** the founder hit a wall — Google AI Studio's paid Gemini key needs a regional *prepay* plan, and the app can't consume a Studio/Vertex key anyway because the `google` LLM provider is an unimplemented stub. Vertex (service-account auth, billed to the $300 credit) is the route that works and stays data-excluded.

---

## What was verified before this brief (so nothing surprises us later)

Confirmed by reading the code, not assumed:

- **Already implemented, do not touch:** `OpenAIEmbeddingProvider` (embeddings), `GoogleTTSProvider` (TTS), `CloudflareImageProvider` (images). These stay as-is.
- **`router.py::GoogleProvider`** is `_UnimplementedProvider` (raises). **← build target 1.**
- **`media/moderation.py`** has only `ClaudeImageModerator` (uses `ANTHROPIC_API_KEY`), instantiated directly at `moderator = ClaudeImageModerator()`. With no Anthropic key, `IMAGE_MODERATION=auto` degrades to structural-only — the vision gate behind the F-1/F-2 companion-injection fix **does not run**. **← build target 2 (safety-critical).**
- **Guards already compatible:** `model_ids.pairing_error` has branches only for `openrouter` (needs slug) and `anthropic` (rejects slug); a bare `gemini-2.5-flash` under provider `google` passes. `is_free_route`/`training_error` pass (no `:free`). Good — no guard changes needed for correctness, but see task 6.
- **`requirements.txt`** has `anthropic`, `openai` — **no Google SDK.** **← build target 3.**
- **`AI_PROVIDER_ORDER=gemini`** in the docs is invalid — the registered provider name is **`google`**. **← task 7 (doc/config fix).**

---

## Scope (build all of it)

### 1. Implement `GoogleProvider` (LLM) on Vertex — `backend/apps/story/ai/router.py`
Replace the `GoogleProvider(_UnimplementedProvider)` stub with a real provider that satisfies the existing `StructuredProvider` protocol (`name`, `is_configured()`, `generate_json(system, messages, schema) -> str`, optional `self.last_usage`, `model` property). Requirements:

- **SDK & auth:** use the **`google-genai`** SDK in **Vertex mode** — `genai.Client(vertexai=True, project=<GOOGLE_CLOUD_PROJECT>, location=<GOOGLE_CLOUD_LOCATION>)`, authenticated by a **service account** via `GOOGLE_APPLICATION_CREDENTIALS` (ADC). No API key. `name = "google"`.
- **`is_configured()`** → true when project + location + credentials are present. This is what `select_provider` keys off, so a mis-set deployment falls through to the next provider rather than crashing a beat.
- **Structured JSON:** request `response_mime_type="application/json"` + `response_schema` from the passed `schema`. Gemini's schema is an **OpenAPI 3 subset** — it rejects things Anthropic/OpenAI accept (`additionalProperties`, `$ref`/`$defs`, some formats). Add a Gemini-specific sanitizer (sibling to `schemas.strip_unsupported_keywords`/`json_schema_for`) that produces a schema Gemini accepts **without changing the beat contract**. Return the raw JSON string.
- **Cost metering (Day 14):** set `self.last_usage = {"input_tokens": prompt_token_count, "output_tokens": candidates_token_count}` from Gemini's `usage_metadata`, **read before any post-call raise** (mirror the Anthropic provider's ordering exactly). A refusal must still be priced.
- **Gemini safety filters:** set `safety_settings` to the **most permissive** level for all categories. Rationale: §13.3 makes *our* independent grader the authority; Gemini over-blocking benign children's story content would be a silent quality/availability failure. Then: if a response is blocked or empty (`finish_reason` = SAFETY/OTHER, no candidate), raise **`ProviderError`** (not a generic exception) so it lands in the Day-5 degradation path (fallback beat), never a dead stream. Keep this inside the `try` so `_transport_error` wraps everything else, exactly like `AnthropicProvider`.
- **Errors:** `except AIError: raise` first (preserve considered refusals), then wrap the rest via `_transport_error(self.name, self._model, exc)`.

### 2. Add a Gemini vision image moderator — `backend/apps/media/moderation.py`
The image gate must run under Google with no Anthropic key.

- Add `GeminiImageModerator` implementing the existing `ImageModerator` protocol (`is_configured()`, `moderate(...) -> (is_safe, reason)`, **raises on provider failure** so `media/safety.py`'s total wrapper turns a failure into a *refusal*, never an unhandled exception).
- Use the same `google-genai` Vertex client, a **vision-capable** Gemini model from `IMAGE_MODERATION_MODEL`, the same rubric/prompt/`MAX_TOKENS` shape as `ClaudeImageModerator`, and the same "provider's own refusal is itself a BLOCK verdict" handling.
- **Selection:** make the active moderator configurable instead of the hardwired `moderator = ClaudeImageModerator()`. Add `IMAGE_MODERATION_PROVIDER` (default keeps existing behaviour; set to `google` for this deploy), or select by which credentials are present — your call, but it must: (a) pick Gemini when Google creds are configured and Anthropic is not, (b) keep `IMAGE_MODERATION=auto`'s "fail-closed, log the gap when no provider is configured" contract intact, (c) still let `prod.py`'s refusal of `IMAGE_MODERATION=off` stand.
- Keep `ClaudeImageModerator` — this is additive, not a replacement.

### 3. Dependencies — `backend/requirements.txt`
Add `google-genai` (pinned). Run `pip-audit -r backend/requirements.txt` — it must report **no known vulnerabilities** (the F-8 CI `Dependencies` job is now blocking). If the pin has an advisory, take the fixed version.

### 4. Settings & auth — `backend/config/settings/base.py` (+ `prod.py`)
Add and document: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` (e.g. `asia-south1` or `us-central1`), `GOOGLE_APPLICATION_CREDENTIALS` (path to the service-account JSON). `GOOGLE_GENAI_USE_VERTEXAI=true` is read by the SDK. In `prod.py`, when `google` is the selected provider (or `IMAGE_MODERATION_PROVIDER=google`), **refuse to boot** if project/location/credentials are missing — same spirit as the other boot guards, plain-ASCII message.

### 5. Cost pricing (optional but do it) — `backend/apps/analytics/pricing.py`
Add Gemini `gemini-2.5-flash` and `gemini-2.5-flash-lite` input/output rates so spans are priced rather than `priced=False`. If left out, `priced=False` is the honest fallback and acceptable — but a real cost/session number needs them.

### 6. Guard hardening (small) — `backend/apps/common/model_ids.py`
`pairing_error` today only knows `openrouter` and `anthropic`. Add a `google` awareness so a stray **slug** (`vendor/model`) set for the `google` provider is caught with a clear message rather than failing at call time. Keep it narrow — shape only. (`is_free_route`/`training_error` already correct for bare Gemini ids.)

### 7. Config & doc fixes (make the plan match the code)
- `.env.example`, `docs/Hosting.md §4`, `docs/Runbooks/Deploy-GCP.md §7`, `docs/Test/test-prerequisites.md`: change `AI_PROVIDER_ORDER=gemini` → **`AI_PROVIDER_ORDER=google`** (the registered name). Set `AI_BEAT_MODEL=gemini-2.5-flash`, `AI_SAFETY_MODEL=gemini-2.5-flash-lite`, `AI_MEMORY_MODEL=gemini-2.5-flash-lite`, `IMAGE_MODERATION_MODEL=<gemini vision model>`, `IMAGE_MODERATION_PROVIDER=google`, plus the three `GOOGLE_CLOUD_*`/credentials vars. State that Vertex is billed to the GCP project (the $300 credit) and is data-excluded (§13.8).

---

## Acceptance criteria (done when ALL true)
1. With Vertex creds set and **no Anthropic/OpenRouter key**, `make hello-beat` generates a real, schema-valid beat via Gemini; `select_provider()` returns `google`.
2. Structured beats parse: the real beat schema round-trips through the Gemini sanitizer and back into a valid `Beat` — proven on the actual beat contract, not a toy schema.
3. A Gemini **safety block / empty candidate** surfaces as `ProviderError` and lands in the fallback-beat degradation path — a child never gets a dead stream. Tested.
4. `self.last_usage` is populated from `usage_metadata`; a metered-then-failed generation is still costed (mirror Day-14 P1-5).
5. `GeminiImageModerator` runs the image gate with **no Anthropic key**: a benign image passes, an unsafe one is blocked, a provider failure becomes a refusal (fail-closed). `IMAGE_MODERATION=auto` with Google creds actually calls Gemini vision (not the structural fallback).
6. Boot guards: `prod.py` refuses to boot with `google` selected but project/location/creds missing; `launch_check` `privacy.provider_terms` is `OK` for the bare Gemini ids; `grader_is_separate` holds (`gemini-2.5-flash` ≠ `gemini-2.5-flash-lite`).
7. `pip-audit` clean with `google-genai` added; the CI `Dependencies` job is green.
8. `make lint && make test && make evals` green; `mypy` clean; docs/`.env.example` updated so `AI_PROVIDER_ORDER=google` and no doc still says `gemini`/free-tier beats.

## Do NOT
- ❌ Don't touch anything the Day-1–15 audit proved clean (cross-account isolation, deletion, ceilings, fallback, budget breaker, ship gate) or the F-1/F-2/F-3 fixes — this is additive.
- ❌ Don't remove `ClaudeImageModerator`, `AnthropicProvider`, or `OpenRouterProvider` — keep the seams; you're filling one in.
- ❌ Don't weaken §13.8: no `:free` routes, and confirm Vertex terms exclude training (they do under GCP enterprise terms — state it, don't just assume in code).
- ❌ Don't set Gemini safety filters strict enough to let Gemini's own filter replace our §13 grader — ours is the authority; translate blocks to the degradation path.

## End state to hand to review
Every child-data LLM call and the image-moderation vision call run on Vertex Gemini under the $300 credit, data-excluded and enforced; structured beats parse; Gemini blocks degrade safely; cost is metered; the image gate runs without Anthropic; guards and docs match the code. Commit with the real CI run id. Then hand `docs/Review/Vertex-Gemini.md`.
