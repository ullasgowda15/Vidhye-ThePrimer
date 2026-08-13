# Review — Vertex Gemini provider (all LLM + image moderation under Google Cloud)

> **Hand to a fresh Claude Code session (repo root), after the build in `docs/Build/Vertex-Gemini.md` is committed.** You are an independent reviewer. This change puts **child data on a new provider and a new SDK, and touches the safety image gate** — so the bar is "proven with a real Vertex call or an honest note that it wasn't", not "the tests pass".
>
> **Source of truth:** `docs/PRODUCT.md` §16.1/§19.3, §13.3, §13.8, §16.6; `docs/Hosting.md §3`; the build brief.

## How to run it
Read the build brief and the diff. Verify against the acceptance criteria with real commands (`make lint`, `make test`, `make evals`, `pip-audit`, `mypy`, `launch_check`). Where a real Vertex call needs credentials you don't have, say so explicitly and verify everything around it (schema sanitizing, error translation, config gating) with a faked client. **Write `docs/Review/reports/Vertex-Gemini-report.md`** with a P0→P1 punch-list. Do not fix during the audit; if the founder authorizes a combined session, fix P0→P1 after the report and re-verify.

## The things most likely to be wrong — press hardest here

1. **Gemini schema rejection (highest risk).** Gemini's `response_schema` is an OpenAPI-3 subset. Confirm the **real beat schema** (not a toy) round-trips through the sanitizer and Gemini accepts it, and that the sanitizer changed the *wire schema only*, not the `Beat` contract the rest of the app validates against. A beat that Gemini 400s on is every story dead. If no live key: assert the sanitizer's output contains no `additionalProperties`/`$ref`/`$defs`/unsupported formats, and that a sanitized-then-parsed sample still validates as a `Beat`.

2. **Safety-block handling (safety + availability).** Gemini has its own content filters. Confirm: (a) safety settings are set permissive enough that our §13 grader stays the authority (Gemini isn't silently refusing benign kids' content); (b) a real block / empty candidate becomes `ProviderError` → the Day-5 fallback beat, **never** a dead stream or an unhandled exception. Drive a stubbed "blocked" response through `create_beat` and confirm the child gets a warm fallback.

3. **The image gate actually runs under Google (the F-1/F-2 recall layer).** With Google creds and **no `ANTHROPIC_API_KEY`**: confirm `IMAGE_MODERATION=auto` calls `GeminiImageModerator`, not the structural fallback. Confirm a provider failure is **fail-closed** (becomes a refusal via `media/safety.py`'s total wrapper, not an exception a child sees). Confirm `prod.py` still refuses `IMAGE_MODERATION=off`. This is the mitigation the audit said was unproven — prove it now.

4. **Cost metering.** `self.last_usage` is set from `usage_metadata` and read **before** any post-call raise (a refusal is still priced — Day-14 P1-5). Spans carry real token counts; if pricing rates were added, cost/session is a real number, else `priced=False` honestly.

5. **§13.8 still holds and is enforced.** `launch_check.privacy.provider_terms` = `OK` for the bare Gemini ids; `training_error` returns `None` for them and still fires for a `:free` route; `grader_is_separate` holds. Confirm Vertex's data-exclusion is *stated* (terms/purchasing fact), not merely assumed by the `:free` check — the guard only catches the cheap mistake.

6. **Auth & boot gating.** `prod.py` refuses to boot when `google` is selected but project/location/service-account creds are missing (plain-ASCII message). `is_configured()` returns false (not raises) on missing creds so `select_provider` falls through cleanly. No service-account JSON path or key is committed or logged.

7. **Dependency hygiene.** `google-genai` pinned; `pip-audit` clean; the CI `Dependencies` job green; `make test`/`make evals` still green after the new dep.

8. **Nothing the Day-1–15 audit proved clean regressed.** Spot-check cross-account isolation, deletion completeness, the ship gate, and the F-1/F-2/F-3 fixes are untouched. `AnthropicProvider`, `OpenRouterProvider`, `ClaudeImageModerator` seams still present.

## Automatic P0
- A child can get a **dead stream / raw error** from a Gemini refusal or block (not degraded to fallback).
- The **image gate silently doesn't run** under the intended Google config (structural fallback where vision was expected).
- Any **child-data model reaching a training-permitted route**, or the boot/preflight guards not catching it.
- Structured beats that **Gemini rejects** in the real schema (product-wide outage).
- A **secret** (service-account JSON, key) committed or logged.

## Deliverable
`docs/Review/reports/Vertex-Gemini-report.md`: verdict (SHIP / FIX-THEN-SHIP / NO-GO), findings table (id/sev/file:line/impact), per-finding proof, what you verified clean, and — stated plainly — **what could not be verified without a live Vertex credential** and exactly which acceptance criteria that leaves open for the founder's real-key manual test. End with the honest bottom line: is this safe to run for a child on Vertex, and what must change first.
