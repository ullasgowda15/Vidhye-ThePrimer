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

---

## The remaining P2s, closed in a follow-up pass

The three items above were where the brief stopped. On review of the report the founder took the remaining ones in the same sitting; this is what changed.

### P2-5 — embeddings are cost-traced (**fixed**)

`cost.record` is documented as being called from the two places money is spent and nowhere else, precisely so a caller cannot forget. Embeddings were the exception that proved it: they go through neither `ModelRouter.generate_structured` nor `media.service._get_or_generate`, so every consolidation run and every branch-point recall was real money `cost/session` did not count.

The span now goes in at **`adapters.embedding.embed()`** — the seam every caller already has to use, which makes it the third and, as long as nothing else calls a vendor directly, the last:

- **`GoogleEmbeddingProvider` gained a `last_usage`**, set from Vertex's own `ContentEmbeddingStatistics.token_count` and read *before* the width checks that can raise, so a call that answered and was then refused still writes its span. Same convention, same ordering and same reasoning as the LLM providers'.
- **A provider with no meter is estimated from characters and marked `estimated: True`**, mirroring `router._trace_llm_cost`. An unmarked estimate on a paid route would read as a measurement.
- **A call that never reached the model writes nothing** — `last_usage is not None` is the test, as in `generate_structured`. Inventing a price for a call that was never billed over-reports spend, which is the same dishonesty as under-reporting it.
- **Priced**: `google:gemini-embedding-001` at `("0.15", "0.00")`, `openai:text-embedding-3-small` at `("0.02", "0.00")`, `local-hash` at a known zero. The zero output rate is a fact — embeddings bill on input only — not a missing entry, which would mean "we do not know".
- **Recorded as `kind=llm, operation="embedding"`.** An embedding *is* a token-billed model call, `_by_operation` keeps it separable in the dashboard, and a fourth `CostKind` would have been a migration on a column whose existing rows would then disagree with the new ones. If that split ever needs to be first-class, it should be made deliberately.

Note the consequence, because it is a behaviour change and not only a metric: `cost.record` feeds `budget.spend`, so embedding spend now counts against the §18.8 breaker. That is correct — the breaker is meant to count exactly what the cost table counts — and it means the cap is now measuring slightly more than it was.

**Proved by** four tests in `TestEmbeddingsAreCostTraced`: a priced span from a real token count, the estimated path marked as estimated, no span when the call never reached the model, and a span that survives a refused-for-width response. The rates are list prices, and `pricing.py`'s docstring already says what that means.

### P2-4 — thinking tokens are now visible (**instrumented, still uncapped**)

The cap itself is still deliberately not set, for the reason the brief gave: the 1,136↔1,572-token spread has to be measured against cost and fallback rate before anything is capped, or the product's own quality is the thing being trimmed.

What changed is that the first live sitting will now actually produce the number. `_gemini_usage` still folds `thoughts_token_count` into the output side — that is right, Gemini bills thinking at the output rate — and now *also* reports it as **`thoughts_tokens`** on the span's `units`. `cost.price` reads `input_tokens` and `output_tokens` only, so the arithmetic is untouched; the number is simply there, in the `cost.span` log line and in the stored row. Absent when the vendor reports no count, rather than stored as a zero we did not measure.

**Proved by** `test_thinking_is_reported_on_its_own_as_well_as_folded_in`, which asserts the key *and* that pricing is identical with and without it, plus `test_a_vendor_that_reports_no_thinking_count_is_not_given_a_zero`.

### P2-6 — written down and put on the launch checklist (**founder decision recorded**)

Not a code fix, and still not one. What was missing was that the question existed at all:

- **`Docs/Hosting.md §3`** now ends with the distinction stated plainly — "not used for training" is not "not retained". Vertex keeps prompts for its own **abuse monitoring** under Google's policy, unaffected by the §13.8 exclusion, which for this product means a child's first name, fears, retrieved memories and the verbatim session digest sit in a Google-side log for that period. That is **§13.7**, a data-logging exemption is the only lever, and the paragraph says outright that it is not a substitute for reading the current terms.
- **`Docs/Runbooks/Launch.md §2`** carries it as an explicit item: read the terms, decide, write the decision down. Either answer is defensible for a closed alpha; not having looked is not.

## The two founder actions — now on the runbook, not only in this report

Both were carried forward from the build's own report, and both are still outstanding — neither is closable from a session. They have moved from prose here to checkboxes in `Docs/Runbooks/Launch.md`, where the pre-launch work actually gets read:

1. **Issue a fresh Vertex service-account key and delete the old one** (`Launch.md §1`). The build's key sat in a OneDrive-synced folder. No key material has ever been committed — the review checked every commit in the history — but a key that has been through a sync client's cache has left the box it was issued for. The runbook makes the deletion explicit: a rotation that leaves the old key valid is a copy.
2. **Set the GCP budget alerts** (`Launch.md §2`, `Hosting.md §5`). Our breaker can only stop what it counts. It counts more now that P2-5 is fixed, but the vendor-side alert is the one that catches a runaway our own meter under-reads, which is exactly why it should live in a different system from ours.

Nothing else in the review's finding table was touched.

---

## Verification

Run here, on each commit. The P1/P2-1/2/3 pass is `5dd9727`; the P2-4/P2-5/P2-6 follow-up is the commit this report is filed with.

| gate | result |
|---|---|
| `make lint` (ruff check + format, backend and evals) | **All checks passed**; 234 + 9 files already formatted |
| `mypy` | **Success: no issues found in 262 source files** |
| backend `pytest` | **1,480 passed**, exit 0 — 1,464 before this work, plus the **10 tests** of the first pass and the **6** of the follow-up |
| frontend `npm run test` | **183 passed** (13 files) — untouched by either pass, run as the other half of `make test` |
| `pytest evals/` — the ship gate | **44 passed, 1 skipped** (the skip is the live-only model grader) |
| `pip-audit -r backend/requirements.txt` | **No known vulnerabilities found** |
| `makemigrations --check --dry-run` | **No changes detected** — no schema change in either pass, including the `kind=llm` decision that avoided one |
| CI (first pass) | **green, run `31762474728`** on `5dd9727` — all five jobs (Backend, Frontend, Evals, Dependencies, Backend image) |
| CI (follow-up) | **green, run `31764232250`** on `c9132b9` — all five jobs |
| CI (final tree) | **green, run `31764795731`** on `e77fae3` — all five jobs. This is the run to quote: it is the last commit of the pass, with this report in it. |

One correction to the record for a future reader, since the run list shows it: the follow-up commit's *own* run (`31764185406`, on `4f09096`) reads **cancelled**, and did not fail. `c9132b9` — a documentation-only commit landing the next session's brief — was pushed while it was in flight, and CI's concurrency rule superseded it. `31764232250` runs the same code plus that one markdown file, and it is green on all five jobs.

The sixteen new tests, by the fix they hold down:

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
| P2-4 | `test_thinking_is_reported_on_its_own_as_well_as_folded_in` | `tests/test_google_provider.py` |
| P2-4 | `test_a_vendor_that_reports_no_thinking_count_is_not_given_a_zero` | `tests/test_google_provider.py` |
| P2-5 | `TestEmbeddingsAreCostTraced::test_a_vertex_embedding_writes_a_priced_span` | `apps/memory/tests/test_embedding.py` |
| P2-5 | `…::test_a_provider_with_no_meter_is_estimated_and_says_so` | `apps/memory/tests/test_embedding.py` |
| P2-5 | `…::test_a_call_that_never_reached_the_model_is_not_priced` | `apps/memory/tests/test_embedding.py` |
| P2-5 | `…::test_a_metered_call_that_is_then_refused_still_writes_its_span` | `apps/memory/tests/test_embedding.py` |

Two existing meter tests changed rather than being added to, because their subject changed: `test_usage_is_read_from_gemini_and_thinking_is_priced` and `test_a_metered_generation_that_is_then_refused_is_still_costed` asserted `last_usage` by whole-dict equality, so the new `thoughts_tokens` key belongs in them. Their pricing assertions are unchanged, which is the point.

The safety axis the review verified clean is untouched and still green: nothing here changes a provider, the orchestrator, the streaming path, the safety classifier, the fallback pool or the moderation wrapper's fail-closed posture. Two of the six changes make a guard *stricter* (P1-1, P2-1) and one makes a preflight row *harder to pass* (P1-2), which is the direction that matters.
