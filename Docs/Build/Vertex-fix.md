# Fix — Vertex review punch-list

> **Hand to a fresh Claude Code session (repo root).** Closes the P1s (and three cheap P2s) in `docs/Review/reports/Vertex-Gemini-report.md`. Verdict there was **FIX-THEN-SHIP, no P0** — the safety axis is sound; these are guard-completeness and template-honesty fixes, all one-file, none needing a live Vertex credential.
>
> **Source of truth:** the review report; `docs/Hosting.md §3/§4`; PRODUCT.md §13.3/§13.8/§16.6.

## Golden rule
**Fix only the items below.** The review verified a large surface *clean* (§§1–11 of the report: schema round-trip, safety-block → fallback, image gate fail-closed, cost metering, §13.8 enforcement, auth/boot, no regression, dimension/normalization). Do not touch any of it except the exact lines named here. Every fix has a test that fails without it and passes with it; `make lint && make test && make evals` green after, CI green on the final commit.

---

## P1-1 — `EMBEDDING_PROVIDER=google` must gate the boot (report P1-1)
`prod.py`'s `_wants_vertex` lists only `AI_PROVIDER_ORDER` and `IMAGE_MODERATION_PROVIDER`. `3841e38` made `google` the **default** for `EMBEDDING_PROVIDER`, so a prod box with no Vertex creds **boots**, then stores every memory with `embedding = NULL` and recall silently degrades to the rules half — forever, with nothing failing.

**Fix:** add `("EMBEDDING_PROVIDER", EMBEDDING_PROVIDER == "google")` to `_wants_vertex` in `prod.py` (`~:98–105`). **Test:** a subprocess boot with `EMBEDDING_PROVIDER=google` and blank `GOOGLE_CLOUD_*`/creds must **refuse** (it currently boots); the all-set config still boots.

## P1-2 — `launch_check` must ask whether a moderator actually exists (report P1-2)
`launch_check`'s `safety.image_gate` row reports on the *string* `IMAGE_MODERATION=auto`, not on whether a moderator is configured — so it prints `OK` while `select_moderator()` returns `None`. This is the founder's go/no-go screen.

**Fix:** in `launch_check` (`~:153–166`), call `moderation.select_moderator()` and **FAIL** the row when `IMAGE_MODERATION=auto` and it returns `None` (mirror the pattern already used by `safety.grader_reachable` eight lines above, which was upgraded from "is a key set" to "ask the provider" for exactly this reason). Optionally mirror the check in `prod.py`. **Test:** `auto` + no vision creds → the row FAILs; the all-Google config → `OK` and `select_moderator()` is a `GeminiImageModerator`.

## P1-3 — `.env.example` must describe ONE deployment (report P1-3)
The template pre-switches only the embedding route to Google while LLM (`AI_PROVIDER_ORDER=anthropic`, `AI_BEAT_MODEL=claude-opus-5`) and image gate (`IMAGE_MODERATION_MODEL=claude-…`, `IMAGE_MODERATION_PROVIDER=auto`) stay Anthropic. Copied verbatim it's P1-1's live failure path, and every other doc (`Hosting.md §4`, `Deploy-GCP §7`, `CLAUDE.md`, `PROGRESS.md`, `manual_testing.md`) says all-Google.

**Fix:** flip `.env.example` to the **all-Google** config every other document describes — `AI_PROVIDER_ORDER=google`, the bare Gemini ids (`gemini-2.5-flash`, `gemini-2.5-flash-lite`), `IMAGE_MODERATION_PROVIDER=google`, `IMAGE_MODERATION_MODEL=gemini-2.5-flash-lite`, the `GOOGLE_CLOUD_*` + `GOOGLE_APPLICATION_CREDENTIALS` block present (values blank for the operator to fill). Keep the Anthropic/OpenRouter values as **commented** alternatives (the seams stay real). **Test:** widen `test_the_documented_configuration_is_the_all_google_one` to assert the LLM + image-gate lines too, not just the three `EMBEDDING_*` lines (today it passes on a Claude LLM).

---

## Cheap P2s to fold in (the review called these latent traps / accuracy — close them now)

- **P2-1 — refuse `allOf`/`oneOf`, don't drop them** (`schemas.py:334–354, 428–430`). They're absent from `Beat`/`StoryPlan` today, but silently dropping them means a future `Literal` union reaches Gemini as a typeless node — the exact silent contract loss `GeminiSchemaError` exists to prevent. Make the sanitizer **raise `GeminiSchemaError`** on an unconvertible `allOf`/`oneOf` instead of dropping it. Test with a toy schema containing `oneOf`.
- **P2-2 — the width guard's message must match reality** (`embedding.py:398–407` vs `memory/models.py:59`). The guard compares against `settings.EMBEDDING_DIMENSIONS` but the column is hard-pinned at `1536`; set the env var to 3072 and the guard passes with a false message before Postgres rejects the insert. Either compare against the true column width or make the message state the pinned 1536. Test: `EMBEDDING_DIMENSIONS=3072` fails loudly with a correct message.
- **P2-3 — fix the comment** (`memory/models.py:57–58`). It says 1536 is "the native width of the shipped provider"; `gemini-embedding-001`'s native width is **3072** and 1536 is a Matryoshka truncation. One-line correction to match every other file.

## NOT in this pass (deliberate)
- **P2-4 (no `thinking_config` cap on LLM routes)** — do **not** blindly cap Gemini's thinking; it could truncate beat quality. Measure the 1,136↔1,572-token spread against cost and fallback rate in the **first live sitting**, then decide. Leave a one-line note in `PROGRESS.md`.
- **P2-5 (embeddings not cost-traced)** — small under-count; touches the Day-8 consolidation path the build had no reason to enter. Leave; note in `PROGRESS.md`.
- **P2-6 (Vertex abuse-monitoring logging / retention — a §13.7 question)** — **founder decision**, not a code fix. Add one honest line to `Hosting.md §3` noting Vertex's own request-logging/retention exists and whether a data-logging-exemption request belongs on the launch checklist; flag it in the report for the founder.

## Founder actions to restate in the fix report (not code)
- **Rotate the Vertex service-account key** (it sat in a OneDrive-synced folder).
- **Set the GCP budget alerts** (`Hosting.md §5`) before a family plays.

## Done when
P1-1/P1-2/P1-3 + P2-1/P2-2/P2-3 fixed, each with a fail-without/pass-with test; nothing the review verified clean regressed; `make lint && make test && make evals` green; `mypy`, `pip-audit` clean; CI green with the real run id. Write `docs/Review/reports/Vertex-fix-report.md` noting what changed, what was left (P2-4/5/6) and why, and the two founder actions.
