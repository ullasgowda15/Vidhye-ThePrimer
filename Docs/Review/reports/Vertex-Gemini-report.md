# Review report — Vertex Gemini provider + Vertex embeddings

**Scope:** `36f1147` (Gemini on Vertex: LLM provider + image moderator), `d50a824` (safety settings from the SDK enums + docs), `3841e38` (embeddings off OpenAI), plus the doc/CI follow-ups `5715d82`, `0ac4ec6`, `a7f66bb`, `c0ce720`. Reviewed against `Docs/Review/Vertex-Gemini.md` §§1–11, both build briefs, and `PRODUCT.md` §13.3 / §13.8 / §16.1 / §16.6 / §11.3.

**Reviewer:** independent session. Did not write either build. Everything below was re-derived by running commands against `main` at `c0ce720`; the build reports were read *after* the checks, not used as evidence.

---

## Verdict: **FIX-THEN-SHIP**

The engineering is sound and the two builds do what they claim. **None of the six automatic-P0 conditions fired** — each was checked individually, not assumed:

| Automatic-P0 condition | Result |
|---|---|
| Child gets a dead stream / raw error from a Gemini block | **Did not fire.** Every decline shape becomes `ProviderError` (an `AIError`); `orchestrator.create_beat` degrades to a warm fallback; `streaming.stream_beat` catches `AIError` and still emits a `done` frame. |
| Image gate silently off under the intended Google config | **Did not fire.** With Google creds and **no `ANTHROPIC_API_KEY`**, `select_moderator()` returns `GeminiImageModerator` — verified directly, not from the report. A provider failure is `FLAGGED`, not an exception. |
| Child-data route on a training-permitted tier | **Did not fire.** All four settings (`AI_BEAT_MODEL`, `AI_SAFETY_MODEL`, `AI_MEMORY_MODEL`, `EMBEDDING_MODEL`) pass `training_error`; `:free` still refuses at boot and in `launch_check`. |
| Structured beats Gemini rejects | **Did not fire.** The **real** `Beat` contract round-trips `gemini_schema` with zero forbidden keywords and validates against `google.genai.types.Schema` (which is `extra="forbid"` — I confirmed that claim rather than taking it). |
| Embedding width mismatch / unnormalized vectors | **Did not fire.** 1536 in, 1536 out, L2 norm `1.000000`, two independent width guards both fire when broken. |
| Secret committed or logged | **Did not fire.** No service-account material anywhere in 81 commits of history; `.env` and every service-account filename pattern are gitignored; only the credential *path* ever appears, in a boot error. |

What holds this back from SHIP is not the Vertex work itself. It is that **the fourth route the second build added — embeddings — did not inherit the two guards the first build wrote for exactly this failure mode**, and that the template a person copies is now half-migrated. All three P1s are small, local, and provable; none require a live credential to fix or to verify.

---

## Findings

| id | sev | file:line | impact |
|---|---|---|---|
| **P1-1** | P1 | `backend/config/settings/prod.py:98–105` | `EMBEDDING_PROVIDER=google` (the **new default**) has no boot guard. Prod starts with no Vertex credentials and every memory is then stored with no vector — semantic recall gone, silently, forever. |
| **P1-2** | P1 | `backend/apps/common/management/commands/launch_check.py:153–166`; `backend/config/settings/prod.py:66–71` | Neither the boot guard nor the preflight requires a *configured* moderator when `IMAGE_MODERATION=auto`. `launch_check` prints `OK safety.image_gate` while `select_moderator()` returns `None`. The founder's go/no-go screen says the gate is on when it is structural-only. |
| **P1-3** | P1 | `.env.example:44, 93–94, 115, 164, 176` | The template is half-migrated: LLM and image gate default to Anthropic, embeddings default to Google. Copied verbatim it produces a deployment whose embeddings cannot run. Every other document (`Hosting.md §4`, `Deploy-GCP §7`, `DEPLOY.md`, `PROGRESS.md`, `manual_testing.md`, `CLAUDE.md`) says all-Google. |
| **P2-1** | P2 | `backend/apps/story/ai/schemas.py:334–354, 428–430` | `allOf` / `oneOf` are **dropped** rather than converted or refused. Harmless today (verified: neither appears in `Beat` or `StoryPlan`), but a future `Literal` union or constrained subclass would be sent to Gemini as a node with a description and no type — the exact class of silent contract loss `GeminiSchemaError` exists to prevent. |
| **P2-2** | P2 | `backend/apps/media/adapters/embedding.py:398–407` vs `backend/apps/memory/models.py:59` | The width guard compares against `settings.EMBEDDING_DIMENSIONS`; the column is hard-pinned at the module constant `1536`. Set the env var to 3072 and the guard passes, its message ("`memory.embedding` is vector(3072)") is false, and Postgres rejects the insert instead. |
| **P2-3** | P2 | `backend/apps/memory/models.py:57–58` | Comment says 1536 is "the native width of the shipped provider". `gemini-embedding-001`'s native width is **3072**; 1536 is a Matryoshka truncation. Every other file states this correctly — this one was missed by the sweep. |
| **P2-4** | P2 | `backend/apps/story/ai/router.py:54, 860–870` | No `thinking_config` on the beat / safety / memory routes (it is set only on the image moderator). The build itself measured 1,136 vs 1,572 output tokens for the *same* prompt — a 38% spread that is entirely thinking — against `MAX_OUTPUT_TOKENS = 8000`. Uncapped thinking is an unmeasured contributor to both cost and the fallback rate. |
| **P2-5** | P2 | `backend/apps/memory/tasks.py:67`; `backend/apps/analytics/cost.py` | Embeddings are not cost-traced. `cost.record` is called only from `ModelRouter.generate_structured` and `media.service._get_or_generate`; the `embed_content` call goes through neither, so `cost/session` under-counts. Self-reported by the build; confirmed. |
| **P2-6** | P2 | `Docs/Hosting.md §3` | §13.8 (no training) is stated as a purchasing fact and enforced in the one shape code can enforce — correct, and §3 is honest that the guard does not prove paid terms. What is not addressed anywhere is Vertex's own **abuse-monitoring logging and its retention**, which is a §13.7 question, not a §13.8 one. Worth one line so the founder knows whether an exemption request belongs on the launch checklist. |

### P1-1 — `EMBEDDING_PROVIDER=google` boots with no credentials

`prod.py` refuses to boot when Vertex is *intended* — but "intended" is defined as two settings only:

```python
_wants_vertex = [
    label
    for label, wanted in (
        ("AI_PROVIDER_ORDER", bool(_order) and _order[0] == "google"),
        ("IMAGE_MODERATION_PROVIDER", IMAGE_MODERATION_PROVIDER == "google"),
    )
    if wanted
]
```

`EMBEDDING_PROVIDER` is not in that list, and `3841e38` made `google` its **default** (`settings/base.py:594`).

Proof — a fresh interpreter importing `config.settings.prod`, with `GOOGLE_CLOUD_*` and `GOOGLE_APPLICATION_CREDENTIALS` explicitly blank (they must be blanked explicitly: the repo-root `.env` is read at boot and otherwise supplies the founder's real values, which is itself worth knowing when reading any of these tests):

```
[BOOTED]  control: anthropic LLM, EMBEDDING_PROVIDER defaults to google, NO Vertex creds
[BOOTED]  explicit EMBEDDING_PROVIDER=google, no Vertex creds
[REFUSED] AI_PROVIDER_ORDER=google, no Vertex creds
[REFUSED] IMAGE_MODERATION=off
[REFUSED] EMBEDDING_MODEL on a :free route
```

The runtime consequence, traced through the code that already exists:
`select_embedding_provider()` raises `MediaConfigurationError` → that is a `MediaError` → `memory/consolidation.py:265` catches `MediaError` and appends no vector → the memory row is written with `embedding = NULL` → `retrieval._nearest` filters on `embedding__isnull=False` and finds nothing → every recall silently falls back to the rules half. Nothing fails. Nothing pages. The moat is just gone.

This is precisely the failure `prod.py`'s own comment describes for the LLM route — *"a half-set Vertex box does not crash… which is why this is a boot failure"* — applied to the one route that was not in the list when the comment was written.

**Fix:** add `("EMBEDDING_PROVIDER", EMBEDDING_PROVIDER == "google")` to `_wants_vertex`. One line, and it makes the guard's own stated rationale complete.

### P1-2 — the image gate: `auto` with no vision credentials is invisible

Two halves of the same gap.

*Boot:* `prod.py` refuses `IMAGE_MODERATION=off` (verified — it still does) and its message now promises the alternative: *"give the deployment credentials for a vision model — an `ANTHROPIC_API_KEY`, or the `GOOGLE_CLOUD_*` settings."* Nothing enforces that promise. A prod box with `IMAGE_MODERATION=auto`, no Anthropic key and no Vertex settings boots:

```
[BOOTED] IMAGE_MODERATION=auto, NO anthropic key, NO google creds (gate would be absent)
```

*Preflight:* `launch_check`'s row asks only what the string says, not whether a moderator exists:

```
FAIL  safety.grader_reachable    no AI provider is credentialled: every classifier call will degrade…
OK    safety.image_gate          IMAGE_MODERATION=auto
      select_moderator() actually returns: None
```

`OK` next to a gate that does not run. The sharpest version of this: `safety.grader_reachable` was upgraded **in `36f1147` itself** from "is an API key set" to "ask the selected provider", for exactly this reason — and `safety.image_gate`, eight lines below it, was left asking the old kind of question.

This is *not* the automatic-P0. Under the intended Google config the gate genuinely runs (verified below), and `AI_PROVIDER_ORDER=google` without credentials is refused at boot, so the realistic all-Google deployment cannot reach this state. It is P1 because the preflight is the thing a founder reads before letting a family in, and on this row it can be wrong.

**Fix:** in `launch_check`, call `moderation.select_moderator()` and `FAIL` when `IMAGE_MODERATION=auto` returns `None`. Optionally mirror it in `prod.py`.

### P1-3 — `.env.example` documents two different deployments

| line | value | which deployment |
|---|---|---|
| 44 | `AI_PROVIDER_ORDER=anthropic` | Anthropic |
| 115 | `AI_BEAT_MODEL=claude-opus-5` | Anthropic |
| 164 | `IMAGE_MODERATION_MODEL=claude-haiku-4-5-20251001` | Anthropic |
| 176 | `IMAGE_MODERATION_PROVIDER=auto` | Anthropic |
| 70/71/75 | `GOOGLE_CLOUD_PROJECT=` `GOOGLE_CLOUD_LOCATION=global` `GOOGLE_APPLICATION_CREDENTIALS=` | blank |
| **93/94** | **`EMBEDDING_PROVIDER=google`** **`EMBEDDING_MODEL=gemini-embedding-001`** | **Google** |

Keeping the template on the Anthropic default with Google as a documented switch is a defensible choice. Pre-switching *one* route is not: copied verbatim, this file is P1-1's live path, and with P1-1 unfixed it boots.

Two supporting facts:

- The Gemini build report's own table claims `.env.example` was set to "`AI_PROVIDER_ORDER=google` (not `gemini`), the Gemini model ids, … `IMAGE_MODERATION_PROVIDER=google`". Only the `gemini`→`google` correction and the commented recipe landed; the uncommented values are still Anthropic. (Build brief `Vertex-Gemini.md` task 7 asked for the uncommented switch.)
- `test_the_documented_configuration_is_the_all_google_one` asserts only the three `EMBEDDING_*` lines, so it passes on a file whose documented LLM is Claude. The test name overclaims what it checks.

**Fix:** pick one. Either flip the template to the all-Google config every other document describes, or comment the two embedding lines back to the Anthropic default alongside the rest. Then widen that test to whichever was chosen.

---

## What I verified clean (with the command, not the claim)

**§1 — Gemini schema rejection, on the real contract.** `json_schema_for(Beat)` → `gemini_schema` produces a 4,793-character schema containing **none** of `$schema $id $comment $defs definitions $ref additionalProperties unevaluatedProperties patternProperties allOf oneOf not if then else discriminator examples title const minimum maximum exclusiveMinimum exclusiveMaximum multipleOf minLength maxLength minItems maxItems uniqueItems`; every `type` is in Gemini's six; every surviving `format` is in the allow-list for its type. It validates against `google.genai.types.Schema` — and I confirmed independently that `Schema.model_config['extra'] == 'forbid'` and that it really does reject `$ref` and an unknown key, so the build's "a keyword nobody thought of still fails" is a true statement about that test and not a hopeful one. Same for `StoryPlan`. The required-but-nullable trio survives correctly:

```
interaction.prompt_audio: nullable=True type=string
interaction.expected:     nullable=True anyOf=[integer, string]
interaction.choices:      nullable=True type=array items=…
required: ['type', 'prompt_audio', 'expected', 'choices']
enum for interaction.type: ['count', 'choose', 'say', 'tap', 'none']
difficulty: {'type': 'number'}   # ge/le stripped for the wire, still validated on the way back
```

And the contract is untouched: a beat shaped exactly like the sanitized schema `Beat.model_validate`s, `extra="forbid"` is still on `_Strict`, `ModelRouter._parse` is unchanged.

One residual, stated plainly: `interaction.expected` goes on the wire as `anyOf` **with no sibling `type`**. That is legal in Gemini's `Schema` proto and the SDK accepts it, and the build's live `hello_beat` exercised this exact node — but "the SDK accepts it" and "Vertex accepts it" are different sentences, and only the founder's live run closes the second. It has been closed once, on `d50a824`.

**§2 — safety-block handling.** Gemini's own filters are `OFF` on all four categories Vertex accepts a text threshold for, built from `types.HarmCategory` / `types.HarmBlockThreshold` (`d50a824`) so an SDK that dropped a constant fails naming it rather than as a 400 on every generation. I checked the installed SDK's enums: `OFF` exists, all four categories exist, and the members *not* set are the image-only ones plus `UNSPECIFIED`, `JAILBREAK` and `CIVIC_INTEGRITY` — so the constant list is right, not merely plausible. `_GEMINI_COMPLETE` is `{"", "STOP", "FINISH_REASON_UNSPECIFIED"}`; every one of the SDK's other 14 finish reasons — `MAX_TOKENS`, `SAFETY`, `RECITATION`, `LANGUAGE`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `MALFORMED_FUNCTION_CALL`, the image ones — becomes `ProviderError`. Prompt-level blocks and empty candidates too. `except AIError: raise` precedes the `_transport_error` wrap, so a considered refusal is not reclassified as transport. End to end, `orchestrator.create_beat` under an all-SAFETY Gemini yields a playable, `policy.check_text`-clean fallback tagged `fallback:`; `streaming.stream_beat` catches `AIError` and emits `done` rather than dying. **No dead stream on any path I could construct.**

The reasoning for turning the vendor filter off is also right, and the build's live grader run is the argument: the grader had to *read* a passage about a wolf tearing a rabbit open in order to refuse it. A vendor filter that can prevent our §13.3 grader from running is not a second guard, it is a hole in the first one.

**§3 — the image gate under Google.** Directly exercised:

```
auto + google creds + NO anthropic key  -> GeminiImageModerator
auto + google creds + anthropic key     -> ClaudeImageModerator   (existing deployments unchanged)
provider=google + creds + no anth key   -> GeminiImageModerator
provider=google, creds MISSING          -> None   (+ an ERROR log, not a silent swap)
IMAGE_MODERATION=off + google creds     -> None   (off still wins)
classify(png) when the provider raises  -> (SafetyStatus.FLAGGED, 'moderator_error:RuntimeError')
```

Fail-closed confirmed at the wrapper: the exception is logged and becomes `FLAGGED`, never propagates, never becomes a pass. `prod.py` still refuses `IMAGE_MODERATION=off`. `ClaudeImageModerator` is kept. `thinking_budget=0` on this route only is correct and load-bearing — a 2.5 model with dynamic thinking and a 100-token ceiling would return no text and fail closed on every illustration in the product.

**§4 — cost metering.** `self.last_usage` is set from `usage_metadata` on the line immediately after `generate_content` and **before** all four raise sites; `thoughts_token_count` is folded into the output side (Gemini bills thinking at the output rate and reports it separately, so omitting it would under-report the priciest generations). A call that never reached the model leaves `last_usage is None`, which is the honest distinction from "answered and we refused it". `pricing.py` has both Gemini rates, so `priced=True` and cost/session is a real number — with the P2-5 caveat that embeddings are outside the trace.

**§5 — §13.8, enforced not assumed.** `CHILD_DATA_MODEL_SETTINGS` is all four settings including `EMBEDDING_MODEL`. `training_error` returns `None` for `gemini-2.5-flash`, `gemini-2.5-flash-lite` and `gemini-embedding-001`, and fires on `:free` for both an LLM and the embedding setting (`prod.py` refuses to boot on either — verified). `pairing_error` accepts a bare Gemini id under `google` and rejects a gateway slug. On the all-Google config, `launch_check` gives `OK privacy.provider_terms`, `OK safety.grader_reachable (google is configured for gemini-2.5-flash-lite)`, `OK safety.grader_is_separate (gemini-2.5-flash-lite ≠ gemini-2.5-flash)`. The exclusion is *stated* as a purchasing fact in `Hosting.md §3` and `vertex.py`'s docstring — and §3 is explicit that the `:free` guard "catches the specific cheap mistake" and does not prove a paid route's terms, which is the honest sentence the brief asked for. It is enforced in the one shape code can enforce: the client is only ever built `vertexai=True` with an explicit service-account credential, and there is no Gemini API-key setting anywhere in `settings/base.py`, so the AI-Studio route is not one this deployment can be configured onto.

**§6 — auth and boot gating.** `vertex.is_configured()` reads three strings, opens no socket, stats no file, and returns `False` rather than raising — so a half-set box falls through `select_provider` instead of raising inside a beat. `GoogleProvider.__init__` accepts and ignores `api_key`, which is the right refusal. `prod.py` refuses each of the three missing settings and a credentials path that is not a file, in plain ASCII (verified by boot). `apps/common/vertex.py` is the single auth path — the embedding provider imports it rather than building a second client, and its `is_configured` is the same function.

**§7 — dependency hygiene.** `google-genai==2.8.0` pinned (held there deliberately: 2.9.0 raises the pydantic floor). `pip-audit -r backend/requirements.txt` → *No known vulnerabilities found*, run here. CI `Dependencies` green on HEAD.

**§8 — no regression.** The two builds touch `apps/story/ai`, `apps/media`, `apps/common`, `apps/analytics`, `apps/memory`, `config/settings`, `conftest`, tests and docs. Nothing in `apps/accounts`, `apps/profiles`, `apps/safety` or `apps/companion` is modified. The two edits inside audit-clean files are both narrow and both improvements: `launch_check._selected_provider` gains a `google` branch (without which an all-Vertex box would be told its grader is unreachable), and `retrieval._nearest` passes `TASK_QUERY` (one line, preventing a correct index searched slightly wrongly forever — I agree with the build that this was worth doing rather than noting). `AnthropicProvider`, `OpenRouterProvider`, `OpenAIProvider`, `ClaudeImageModerator`, `OpenAIEmbeddingProvider`, `LocalHashEmbeddingProvider` and `VoyageEmbeddingProvider` are all still present and still selectable.

**§9 — dimension and the no-migration claim.** `EMBEDDING_DIMENSIONS = 1536` unchanged. **No new migration** on `memory.embedding`: `apps/memory/migrations/` still ends at `0003_interestedge_worldplace`, the column is the `vector(1536)` from `0001_initial`, the `memory_embedding_hnsw` / `vector_cosine_ops` index is untouched, and `manage.py makemigrations --check --dry-run` reports *No changes detected*. Both width guards fire when broken:

```
provider guard: 'gemini-embedding-001' returned 768 dimensions for an output_dimensionality of 1536.
adapter guard:  provider 'liar' returned 768 dimensions, but memory.embedding is vector(1536).
```

**§10 — normalization.** Driven through the real `GoogleEmbeddingProvider` with a faked Vertex client returning a deliberately non-unit vector: raw L2 `1.146745` in, stored L2 `1.000000` out, length 1536. `output_dimensionality=1536` and `task_type` are both on the wire; `TASK_DOCUMENT` when storing, `TASK_QUERY` from `memory.retrieval` — I confirmed both by inspecting the recorded `EmbedContentConfig`. The build's account of *why* normalization is right is also correct and worth keeping: cosine is magnitude-invariant, so this is not a ranking bug fix, it is keeping the column a set of unit vectors. Saying so rather than repeating the comfortable myth is the right call.

**§11 — all-Google, no OpenAI.** `EMBEDDING_PROVIDER=google` with `OPENAI_API_KEY` blank selects `GoogleEmbeddingProvider`; with no Vertex creds it raises a `MediaConfigurationError` naming the three settings (rather than silently downgrading). `privacy.provider_terms` stays `OK`. One auth path, not two. No document presents `OPENAI_API_KEY` as required — `Hosting.md`'s checklist strikes the OpenAI line explicitly, `README`/`DEPLOY`/`CLAUDE.md`/`PROGRESS` all match. The `openai` package stays in `requirements.txt`, correctly, because the OpenRouter path *is* the OpenAI SDK.

**Secret hygiene.** `git grep` for `BEGIN PRIVATE KEY`, `private_key_id` and `"type": "service_account"` across all 81 commits returns exactly one file — `backend/tests/test_settings_validation.py`, whose fixture writes the literal string `{"type": "service_account"}` to a `tmp_path`. No key material has ever been committed. `.gitignore` gained `*service-account*.json`, `*service_account*.json`, `*-sa.json`, `gemini-key.json`, `vertex-sa.json`, `gcp-credentials*.json`; `gemini-key.json` is confirmed ignored and no longer present at the repo root. Nothing logs the credential; `prod.py` echoes the *path* in a boot error, which the operator needs and which is not a secret. The build's own note that the key sat in a OneDrive-synced folder and is being rotated is the right call and should be finished.

**Toolchain and suite, run here on `c0ce720`.** `pytest` (backend) → **1,464 passed**, exit 0 — the same count both build reports state, and the reason §8 above can say "no regression" as a fact rather than an inference: the cross-account isolation, deletion-completeness, ceiling, fallback, budget-breaker and ship-gate tests are all inside it. `pytest evals/` → **44 passed, 1 skipped**, the ship gate green. `ruff check` → *All checks passed*. `mypy` → *Success: no issues found in 262 source files*. `pip-audit` → *No known vulnerabilities found*. `makemigrations --check` → *No changes detected*.

**CI.** HEAD (`c0ce720`) is green on all five jobs (run `31730766265`). One correction to the record for a future reader: the embeddings commit's *own* run (`31722071810`) was **red** — on `npm audit` only, over a `nanoid` advisory in the Vite dev tree, with Backend, Frontend, Evals and Backend-image all green. `0ac4ec6` fixed it and run `31729138215` is green. The build report states this accurately; I confirmed it against the API rather than taking it.

---

## What could not be verified without a live Vertex credential

I have no Google credential, so everything below was exercised against fakes built from the real SDK response types, and everything below is therefore **owed to the founder's real-key session**. The build was honest about each of these; I am restating them as the reviewer's list, and adding two.

| # | Owed | Acceptance criterion it leaves open |
|---|---|---|
| 1 | **A genuine Gemini refusal has never been observed.** Every block tested is a stubbed response. What is proven is that *our translation* of a block is correct; what is not is that a real block arrives in the shape we translate. With the filters off, the remaining triggers are `PROHIBITED_CONTENT` and `RECITATION`, both hard to provoke with children's-story prompts. | Gemini AC 3 |
| 2 | **The image gate has never blocked a live picture, and has never seen real generated art.** A 1×1 PNG passed. Closes under `make evals-live` / the manual sitting with real Cloudflare illustrations. | Gemini AC 5 |
| 3 | **No full sitting has run on Vertex.** One beat, two safety grades, one image scan — one call each of three of the four types. The **memory digest has never been exercised live at all**. Latency under `MEDIA_TURN_BUDGET_S`, real cost/session, and the truncation question in P2-4 all need a sustained run. | Gemini AC 1, 8 (partially) |
| 4 | **Semantic recall.** The build reports 3/3 correct first-place rankings on lexically-disjoint queries against a `local-hash` control that got 1/3 degenerately — which is exactly the right experiment, and the control is what makes it credible. I can verify the code path, the width, the normalization and the task hints; **I cannot verify that result.** Re-run it. | Embeddings AC 2 |
| 5 | **`gemini-embedding-001` served at the deployment's region.** Reported live at `global` plus three others. Unverifiable here, and the Flash-Lite/`asia-south1` lesson (`models.get()` says present, `generate_content` 404s) means it must be probed with a real `embed_content`, which is what the build says it did. | Embeddings AC 4 |
| 6 | **Vertex accepts the `anyOf`-with-no-`type` node** in `interaction.expected`. Legal in the proto, accepted by the SDK, and exercised by the live `hello_beat` on `d50a824` — but this is the single node most likely to be the difference between "the SDK accepted it" and "Vertex accepted it". Any change to `Interaction` should re-run one live beat. | Gemini AC 2 |
| 7 | **`GOOGLE_CLOUD_LOCATION=global` still serves all four models** (`gemini-2.5-flash`, `gemini-2.5-flash-lite`, the vision call, `gemini-embedding-001`). Region availability is a vendor fact that changes without notice, and the Flash-Lite finding is the proof it matters. Worth re-probing at deploy time, not only at build time. | Gemini AC 1/6, Embeddings AC 4 |

---

## Bottom line

**Is this safe to run for a child on Vertex?** On the safety axis, yes. The child cannot get a dead stream from a Gemini block — I tried to construct one through the provider, the orchestrator and the SSE generator and could not. The image gate really does run on Google credentials alone, and really does fail closed. Our §13.3 grader is still the authority, and turning Gemini's own filter off is the right call for the reason the build gives — a vendor filter that can stop the grader reading the passage it must judge is not a second guard. Every child-data route is on a bare paid Vertex id, guarded at boot and at preflight, with no OpenAI account anywhere in the supply chain and no key ever committed.

**What must change first.** Three P1s, all one-file, none needing a credential:

1. **P1-1** — add `EMBEDDING_PROVIDER` to `prod.py`'s `_wants_vertex`. The second build made `google` the default for the fourth child-data route and did not extend the guard the first build wrote for exactly this. Today a prod box can start, serve stories, and quietly never remember anything.
2. **P1-2** — make `launch_check.safety.image_gate` ask `select_moderator()`. A preflight row that says `OK` while the gate is off is worse than no row, and the fix is the same one already applied to `grader_reachable` eight lines above it.
3. **P1-3** — make `.env.example` describe one deployment. Right now it describes two, and the seam runs straight through P1-1.

Fix those three, re-run the suite, and the remaining risk is the honest kind: seven things a live credential has to say, of which the build has already said five once and needs to say them again under load. The P2s are all real but none of them should hold a family back — P2-1 and P2-2 are latent traps worth closing while the context is fresh, P2-4 is a question the first live sitting will answer, and P2-3/P2-5/P2-6 are accuracy rather than behaviour.

Two non-code items carried forward from the build's own report and still outstanding: **rotate the service-account key** (it sat in a synced folder), and set the GCP budget alerts `Hosting.md §5` calls for, before a family plays.
