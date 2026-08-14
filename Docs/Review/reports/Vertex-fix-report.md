# Fix report — the Vertex review punch-list

**Brief:** `Docs/Build/Vertex-fix.md`. **Findings:** `Docs/Review/reports/Vertex-Gemini-report.md` (verdict **FIX-THEN-SHIP, no P0**).

**Scope held to.** Six items: the three P1s and the three cheap P2s the brief named. Nothing the review verified clean (its §§1–11 — schema round-trip, safety-block → fallback, image-gate fail-closed, cost metering, §13.8 enforcement, auth/boot, no regression, dimension and normalization) was touched except the exact lines named below. No live Vertex credential was needed for any of it, and none was used.

Every fix was checked the same way: revert the source change, run the new test, watch it fail; restore, watch it pass. That loop is recorded per-item under **Proved by**.

---

## P1-1 — `EMBEDDING_PROVIDER=google` now gates the boot

**`backend/config/settings/prod.py`** — one line added to `_wants_vertex`:

```python
("EMBEDDING_PROVIDER", EMBEDDING_PROVIDER == "google"),
```

plus the comment above it, which now says why this route is in the list, and the error message, which now names the third consequence (`every memory is stored with no vector, which turns semantic recall off for good`) alongside the fallback beats and `moderation=absent`.

This is the quietest of the three routes and the reason it was worth a P1. `google` is the *default* for `EMBEDDING_PROVIDER`, so a box reaches this state by saying nothing at all: `select_embedding_provider()` raises `MediaConfigurationError` → `consolidation` catches the `MediaError` and writes the memory without a vector → `retrieval._nearest` filters on `embedding__isnull=False` and finds nothing. Every sitting remembered, no sitting ever recalled semantically, nothing failing and nothing paging.

**Proved by** `tests/test_settings_validation.py::test_the_embedding_route_alone_is_enough_to_require_vertex` (a subprocess boot with `EMBEDDING_PROVIDER=google` and the Vertex trio blank must refuse, in ASCII, naming what the silence costs) and `::test_google_embeddings_with_the_credentials_set_boot` (the same box with credentials still boots — the guard is a list, not a hard no; an Anthropic-LLM deployment embedding on Vertex stays legal). Reverting `prod.py` fails the first.

**One thing this fix exposed, worth knowing.** `PROD_ENV` in that file inherited the repository's own `.env` for every key it did not pin — `settings/base.py` reads it at boot — so this test harness was quietly a *Vertex* deployment on the founder's laptop and a different one in CI. `EMBEDDING_PROVIDER`, `IMAGE_MODERATION_PROVIDER` and the three `GOOGLE_CLOUD_*` settings are now pinned in `PROD_ENV`, and `_vertex_env` names all four child-data routes rather than three. Without that, the new guard made three pre-existing tests fail on this machine and pass in CI, which is the same class of drift the review was written about.

## P1-2 — `launch_check` asks whether a moderator exists

**`backend/apps/common/management/commands/launch_check.py`** — `safety.image_gate` now calls `moderation.select_moderator()`:

| state | before | after |
|---|---|---|
| `auto`, a provider credentialled | `OK  IMAGE_MODERATION=auto` | `OK  IMAGE_MODERATION=auto, and anthropic-vision is configured to look at every generated picture` |
| `auto`, nothing credentialled | `OK  IMAGE_MODERATION=auto` | `FAIL  ... no vision provider is credentialled ... every scan logs moderation=absent (13.8)` + what to set |
| `off` / other | unchanged | unchanged |

Exactly the upgrade `safety.grader_reachable` got eight lines above — stop asking what the setting says, ask the thing that would run. The `OK` row now names the moderator, which is the difference between "the setting is `auto`" and "Claude's vision model will look at every illustration".

**Not mirrored into `prod.py`**, which the brief left optional. `select_moderator()` reads Django settings and imports `apps.media`, and calling it from inside a settings module mid-import is a real circular-import hazard for a guard that would fire only on a configuration `launch_check` already refuses. The preflight is where this belongs: it is the screen a founder reads before letting a family in, and that is where the review found the false `OK`.

**Proved by** three tests in `apps/common/tests/test_launch_check.py`: `test_an_image_gate_with_nothing_to_run_blocks_the_launch` (asserts `select_moderator() is None` *and* that the row FAILs — the premise, not just the verdict), `test_a_configured_image_gate_says_which_model_looks_at_the_picture`, and `TestTheAllGoogleDeployment::test_the_image_gate_runs_on_the_vertex_credentials_alone` (no Anthropic key anywhere, `GeminiImageModerator`, `OK`). Reverting the command fails all three.

The `launchable` and `all_google` fixtures now pin `IMAGE_MODERATION_PROVIDER` for the same reason `PROD_ENV` does — a row that resolves a *provider* cannot be left reading the developer's `.env`.

## P1-3 — `.env.example` describes one deployment

Flipped to the all-Google configuration every other document already describes (`Hosting.md §4`, `Deploy-GCP §7`, `DEPLOY.md`, `PROGRESS.md`, `manual_testing.md`, `CLAUDE.md`):

| line | was | now |
|---|---|---|
| `AI_PROVIDER_ORDER` | `anthropic` | `google` |
| `AI_BEAT_MODEL` | `claude-opus-5` | `gemini-2.5-flash` |
| `AI_SAFETY_MODEL` | `claude-haiku-4-5-20251001` | `gemini-2.5-flash-lite` |
| `IMAGE_MODERATION_PROVIDER` | `auto` | `google` |
| `IMAGE_MODERATION_MODEL` | `claude-haiku-4-5-20251001` | `gemini-2.5-flash-lite` |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | already Google | unchanged |
| `GOOGLE_CLOUD_*`, `GOOGLE_APPLICATION_CREDENTIALS` | present, blank | unchanged (blank, for the operator) |

**The seams stay real.** Every Anthropic and OpenRouter value survives as a commented alternative one comment character away, and `ANTHROPIC_API_KEY=` / `OPENROUTER_API_KEY=` are still there with a line saying they are *not* required by the configuration above because Vertex is authenticated by the service account. `IMAGE_MODERATION_PROVIDER` gained the reason it is named rather than left on `auto`: `auto` takes the first provider with credentials, Anthropic first, so on a box with a stray Anthropic key it silently runs a different model from the one `IMAGE_MODERATION_MODEL` names. The stale "To switch the whole product onto it:" recipe — which described what the file now *is* — became a statement of the configuration plus what `prod.py` refuses.

**Proved by** `apps/memory/tests/test_embedding.py::test_the_documented_configuration_is_the_all_google_one`, widened from three `EMBEDDING_*` assertions to all four routes plus the credential block (it previously passed on a file whose documented LLM was Claude — the review's point that the test name overclaimed), and a new `test_the_other_providers_survive_as_commented_alternatives`, which fails if the flip is ever done by *deleting* the alternatives rather than commenting them. Reverting `.env.example` fails both.

## P2-1 — `allOf` / `oneOf` are refused, not dropped

**`backend/apps/story/ai/schemas.py`** — both keywords moved out of `_GEMINI_UNSUPPORTED_KEYWORDS` (the drop-list) into a new `_GEMINI_UNCONVERTIBLE_KEYWORDS`, which raises `GeminiSchemaError` naming the keyword and what to do instead.

The distinction is the point: dropping `title` or `additionalProperties` loosens the wire schema and costs nothing, because what comes back is validated against the Pydantic model anyway. A union *is* the field's meaning, so dropping one sends Gemini a description with no `type`, the request succeeds, and the model fills the node in by guesswork — a silent contract loss on a request that never errors, which is the one shape a 400 would never reveal. `GeminiSchemaError`'s docstring now names three causes rather than two, and says why the third is the worst of them.

Verified as a no-op for what we actually generate: none of the six contracts that reach a provider — `Beat`, `StoryPlan`, `SessionMemories`, `SafetyVerdict`, `SessionHighlight`, `ReturningGreetingText` — emits either keyword under the pinned Pydantic 2.10.4, checked directly rather than assumed. The whole existing schema suite is unchanged and green, including the review's own `test_no_reference_survives_anywhere_in_the_tree`.

**Proved by** `tests/test_google_provider.py::...::test_a_union_gemini_cannot_express_is_refused_rather_than_dropped`, parametrized over both keywords with a toy schema. Reverting the sanitizer fails both parameters.

## P2-2 — the width guard compares against the column

**`backend/apps/media/adapters/embedding.py`** — `embed()` now checks `settings.EMBEDDING_DIMENSIONS` against `apps.memory.models.EMBEDDING_DIMENSIONS` (the column's pinned 1536) and raises `MediaConfigurationError` when they disagree, then checks the provider's output against the **column** rather than the setting.

Before: set the env var to 3072 and the provider was asked for 3072, returned 3072, and passed a guard whose message then claimed the column was `vector(3072)`. Postgres rejected the insert at the end of a consolidation run instead — a true failure delivered late, behind a false message.

The new check runs **before** the provider call, so a misconfigured box does not spend money producing vectors nothing could store. The imports stay one-directional: it is a function-local import of a module constant, so `apps.media` gains no import-time dependency on `apps.memory`.

**Proved by** `apps/memory/tests/test_embedding.py::...::test_a_width_the_column_cannot_hold_fails_before_anything_is_spent`, which asserts the message names both numbers and the migration, and that the provider was never called. Reverting the adapter gives `DID NOT RAISE`.

## P2-3 — the comment says where 1536 comes from

**`backend/apps/memory/models.py:56–60`** — the constant's comment claimed 1536 was "the native width of the shipped provider". `gemini-embedding-001`'s native width is **3072**; 1536 is a Matryoshka truncation, and that is exactly the fact which makes "no migration, no index rebuild" true. Corrected to match every other file.

**Proved by** `test_the_column_comment_says_where_1536_comes_from`, which reads the module source. A documentation test rather than a behavioural one, on purpose: the comment is the whole artefact, and it will be read by whoever next considers changing the number.

---

## Left undone, deliberately

| id | what | why, and what closes it |
|---|---|---|
| **P2-4** | No `thinking_config` cap on the beat / safety / memory routes. | **Do not cap blind.** The build measured 1,136 vs 1,572 output tokens for the *same* prompt — a 38% spread that is entirely thinking — against `MAX_OUTPUT_TOKENS = 8000`. A cap chosen without data could truncate beat quality, which is the product. Measure the spread against cost and the fallback rate in the **first live sitting**, then decide. Noted in `PROGRESS.md`. |
| **P2-5** | Embeddings are not cost-traced, so `cost/session` under-counts. | Small, and on the Day-8 consolidation path this build had no reason to enter. `cost.record` is called from `ModelRouter.generate_structured` and `media.service._get_or_generate`; `embed_content` goes through neither. Size it from the same live sitting. Noted in `PROGRESS.md`. |
| **P2-6** | Vertex's own abuse-monitoring logging and its retention (a §13.7 question, not the §13.8 one `Hosting.md §3` already answers honestly). | **Founder decision, flagged rather than written.** §13.8 — inputs excluded from training — is settled and is stated as the purchasing fact it is. What is unaddressed anywhere is that Vertex retains prompts for abuse monitoring under its own policy, and whether a **data-logging-exemption request** belongs on the launch checklist. That is a call about what to tell parents and what to ask Google for, not a code fix; see below. |

Nothing else in the review's finding table was touched.

## Two founder actions, restated

Both are carried forward from the build's own report and both are still outstanding. Neither is code, and neither is closable from this session.

1. **Rotate the Vertex service-account key.** It sat in a OneDrive-synced folder. No key material has ever been committed (the review checked all 81 commits), and the `.gitignore` rules for it are a safety net rather than the plan — but a key that has been in a sync client's cache is a key that has left the box. Issue a new one, point `GOOGLE_APPLICATION_CREDENTIALS` at it outside the repository, and delete the old one in the GCP console.
2. **Set the GCP budget alerts** (`Hosting.md §5`) **before a family plays.** `INFERENCE_BUDGET_USD_PER_WINDOW` is our own breaker and it does its job — over the cap children get cached art and pre-vetted warm beats, never an error — but it does not know what Google is billing. The budget alert is the one that catches a runaway that our own meter under-counts, which, per P2-5, it currently does by the embedding share.

**And one decision to make:** P2-6. Read Vertex's abuse-monitoring retention terms and decide whether to file a data-logging-exemption request before the pilot. If the answer is yes, it belongs on `Docs/Runbooks/Launch.md`; either way `Hosting.md §3` should end up with one honest sentence about it, written by the person who made the call.

---

## Verification

Run here, on the fix commit:

| gate | result |
|---|---|
| `make lint` (ruff check + format, backend and evals) | **All checks passed**; 234 + 9 files already formatted |
| `mypy` | **Success: no issues found in 262 source files** |
| backend `pytest` | **1,474 passed**, exit 0 — 1,464 before this pass plus exactly the **10 new tests** below |
| frontend `npm run test` | **183 passed** (13 files) — untouched by this pass, run as the other half of `make test` |
| `pytest evals/` — the ship gate | **44 passed, 1 skipped** (the skip is the live-only model grader) |
| `pip-audit -r backend/requirements.txt` | **No known vulnerabilities found** |
| `makemigrations --check --dry-run` | **No changes detected** — no schema change in this pass |
| CI | **green, run `CI_RUN_ID`** |

The ten new tests, by the fix they hold down:

| fix | test | file |
|---|---|---|
| P1-1 | `test_the_embedding_route_alone_is_enough_to_require_vertex` | `tests/test_settings_validation.py` |
| P1-1 | `test_google_embeddings_with_the_credentials_set_boot` | `tests/test_settings_validation.py` |
| P1-2 | `test_an_image_gate_with_nothing_to_run_blocks_the_launch` | `apps/common/tests/test_launch_check.py` |
| P1-2 | `test_a_configured_image_gate_says_which_model_looks_at_the_picture` | `apps/common/tests/test_launch_check.py` |
| P1-2 | `test_the_image_gate_runs_on_the_vertex_credentials_alone` | `apps/common/tests/test_launch_check.py` |
| P1-3 | `test_the_documented_configuration_is_the_all_google_one` (widened, not new) | `apps/memory/tests/test_embedding.py` |
| P1-3 | `test_the_other_providers_survive_as_commented_alternatives` | `apps/memory/tests/test_embedding.py` |
| P2-1 | `test_a_union_gemini_cannot_express_is_refused_rather_than_dropped[oneOf]` / `[allOf]` | `tests/test_google_provider.py` |
| P2-2 | `test_a_width_the_column_cannot_hold_fails_before_anything_is_spent` | `apps/memory/tests/test_embedding.py` |
| P2-3 | `test_the_column_comment_says_where_1536_comes_from` | `apps/memory/tests/test_embedding.py` |

The safety axis the review verified clean is untouched and still green: nothing here changes a provider, the orchestrator, the streaming path, the safety classifier, the fallback pool or the moderation wrapper's fail-closed posture. Two of the six changes make a guard *stricter* (P1-1, P2-1) and one makes a preflight row *harder to pass* (P1-2), which is the direction that matters.
