# Build report — Vertex Gemini provider

> Brief: `Docs/Build/Vertex-Gemini.md`. Reviewer: `Docs/Review/Vertex-Gemini.md`.
> Self-reviewed against the review brief; **not** independently reviewed.
>
> Build commit `36f1147` — **CI green, run `31707874319`**, all five jobs (Backend, Backend image, Dependencies, Evals, Frontend): ruff clean both sides, mypy 0 issues, no missing migrations, committed OpenAPI schema not stale, `pip-audit` no known vulnerabilities, 1450 backend + 183 frontend + 44 passed/1 skipped evals — matching the local run exactly. A follow-up commit builds the Gemini safety settings through the SDK's own enums rather than strings and adds this report's self-review; its run is recorded at the foot of this file.

## The three things worth knowing before anything else

**1. `asia-south1` cannot run this product, and the plan said to use it.** Verified with real generation calls against the founder's own project, not read off a docs page:

```
model                    asia-south1  asia-southeast1  us-central1  global
gemini-2.5-flash         OK           OK               OK           OK
gemini-2.5-flash-lite    404          404              OK           OK
```

`Hosting.md §11.1` recommended `asia-south1` (Mumbai) as nearest the pilot's users. Flash-Lite is the safety grader **and** the memory digest **and** the image moderator, so on that region every one of them 404s on every call — and because §13.3 requires the grader to be a different model from the writer, there is no single-model configuration that rescues it. `GOOGLE_CLOUD_LOCATION=global` is now what every document says; it serves both, and Google routes to the nearest healthy region.

The trap that hid this is worth repeating, because the obvious probe gives the wrong answer: **`client.models.get()` reports Flash-Lite as present in `asia-south1`** (it resolves against the global publisher catalogue) and `generate_content` then 404s. Probe with a real generation.

**2. The service-account key was found at the repo root, untracked but not gitignored.** `C:\...\Vidhye-ThePrimer\gemini-key.json`, listed by `git status`, one `git add -A` from a history that cannot be rewritten. `.gitignore` covered `*.pem`, `*.key`, `.env*` and `secrets/` — a service-account key is a `.json`, and none of them matched. Never committed (`git log --all -- gemini-key.json` is empty). Moved to `~/.config/primer/vertex-sa.json` outside both the repo and OneDrive, ACL'd to the owner alone; `.gitignore` gained patterns as a safety net. **It sat in a OneDrive-synced folder, so a cloud copy almost certainly exists and moving it locally does not un-upload it** — the founder has chosen to rotate the key after this verification, and that action is still outstanding.

**2b. The new `pairing_error` branch caught a real mistake within minutes, unplanned.** Booting the founder's own `.env` with `AI_PROVIDER_ORDER=google` refused to start:

```
AI_MEMORY_MODEL='google/gemma-4-26b-a4b-it' looks like a gateway model slug, but
google is the provider that will be selected (AI_PROVIDER_ORDER=['google']).
```

A leftover OpenRouter slug, exactly the half-migrated `.env` the guard was written for. Left unguarded it is not a crash: `memory.consolidate` treats an unreachable model as a *degradation*, so every sitting would have consolidated on its rules half only, every night, with a warning in a worker log. The same slug in `AI_SAFETY_MODEL` would have been §13.3's guard silently off — which is how this product failed once already, under OpenRouter, on Day 7.

**3. `google-genai` is pinned at 2.8.0, not the latest 2.18.0.** 2.9.0 raised its floor to `pydantic>=2.12.5`; taking it would have dragged pydantic — the library every generated contract is validated by — up two minor versions as a side effect of adding a provider. 2.8.0 is the newest release that runs on the pinned pydantic 2.10.4, and `pip-audit` reports no advisory against it. The pair should be revisited together, deliberately.

---

## What landed

| Area | File | What |
|---|---|---|
| LLM provider | `backend/apps/story/ai/router.py` | `GoogleProvider` replaces the `_UnimplementedProvider` stub. `google-genai` in Vertex mode, structured JSON via the sanitizer below, cost metered from `usage_metadata` **before** any raise, Gemini's own filters off, every block/empty candidate translated to `ProviderError`. Error ordering mirrors `AnthropicProvider` exactly (`except AIError: raise` first, everything else through `_transport_error`). |
| Schema | `backend/apps/story/ai/schemas.py` | `gemini_schema()` — a sibling of `strip_unsupported_keywords`/`json_schema_for`. Inlines `$ref`/`$defs`, turns `anyOf`-with-null into `nullable`, drops `additionalProperties`/`title`/unknown `format`s, converts `const` to a one-member `enum`, adds `propertyOrdering`. Raises `GeminiSchemaError` on a recursive or dangling reference rather than looping. **Wire schema only — the `Beat` contract is untouched.** |
| Vertex client | `backend/apps/common/vertex.py` *(new)* | One place that knows what "configured" means, how the credential is obtained (service-account file, passed explicitly — never ambient ADC), and caches the client so a token round-trip is not added to every beat. `is_configured()` opens no socket and stats no file: it is called in front of every generation. |
| Image gate | `backend/apps/media/moderation.py` | `GeminiImageModerator` + `select_moderator()` driven by `IMAGE_MODERATION_PROVIDER` (`auto` \| `anthropic` \| `google`). `auto` tries Anthropic first, so an existing deployment is unchanged; a named-but-unconfigured provider leaves the gate absent with a log line rather than silently using the other one. `ClaudeImageModerator` kept. |
| Settings | `backend/config/settings/base.py` | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`, `IMAGE_MODERATION_PROVIDER`. `_selected_ai_provider` learned about Vertex's three-setting credential — without that, `_check_model_pairing` silently checked nothing on the one deployment shape it was extended for. |
| Boot guard | `backend/config/settings/prod.py` | Refuses to boot when `google` is first in `AI_PROVIDER_ORDER` or named as the moderator and any of the three is missing — **or the credentials file is not there**. Plain ASCII. |
| Preflight | `backend/apps/common/management/commands/launch_check.py` | `safety.grader_reachable` asks `_selected_provider()` instead of "is an API key set", so an all-Vertex box is not told its grader is unreachable. `_selected_provider` learned about Vertex. |
| Pricing | `backend/apps/analytics/pricing.py` | `google:gemini-2.5-flash` (0.30/2.50), `google:gemini-2.5-flash-lite` (0.10/0.40) per 1M tokens. |
| Guard | `backend/apps/common/model_ids.py` | `pairing_error` rejects a gateway slug under `google`. Shape only. |
| Dependency | `backend/requirements.txt` | `google-genai==2.8.0`. |
| Config/docs | `.env.example`, `Docs/Hosting.md`, `Docs/Runbooks/Deploy-GCP.md`, `Docs/Test/test-prerequisites.md`, `.gitignore` | `AI_PROVIDER_ORDER=google` (not `gemini`), the Gemini model ids, the `GOOGLE_CLOUD_*` block, `IMAGE_MODERATION_PROVIDER=google`, the region finding, the key-handling rule, and the resolution of `Hosting.md §11`'s open decision 4. |

### Two judgement calls the reviewer should press on

**Thinking is left at Gemini's default on the beat path and switched off in the moderator.** The moderator answers one word inside a 100-token budget; a Gemini 2.5 model with dynamic thinking would spend all hundred reasoning and return no text, which fails closed — meaning *every* generated illustration in the product flagged and replaced with stock art, with only a `ModerationUnavailable` in a log to say why. So `thinking_budget=0` there. On the beat path a wrong value is a hard 400 on every generation (models differ in whether they permit 0), and truncation degrades to a fallback beat rather than to a permanently broken gate, so the vendor default stands. It is a real cost knob, and the size of it is now measured: two runs of the *same* prompt spent 1,136 and 1,572 output tokens, a 38% spread that is entirely thinking, billed at the output rate.

**Gemini's own safety filters are set to `OFF` for the four categories Vertex accepts a threshold for.** This looks like the wrong direction and is the opposite: §13.3 makes our grader the authority, a vendor filter tuned for a general audience has no idea the wolf in a bedtime story is the point, and when it fires we get a blocked response with a category name instead of a graded refusal — a silent availability failure on the surface where the child is waiting. Blocks that happen anyway (unfilterable categories, a policy change) become `ProviderError` and land in the Day-5 degradation path. The image moderator deliberately keeps Gemini's default filters, because there a refusal is a second opinion on exactly the question being asked.

**A live run vindicated that setting in the way that matters.** `safety.classifier` on `gemini-2.5-flash-lite` via Vertex, against real text:

```
benign   -> action=pass  category='low'
unsafe   -> action=block category='critical' cats=['violence']
            reason: "The beat depicts graphic violence, injury, and blood."
cost.span operation=safety label=google:gemini-2.5-flash-lite
          units={'input_tokens': 807, 'output_tokens': 45} usd=0.0000987 priced=True
```

The grader had to *read* a passage about a wolf tearing a rabbit open in order to refuse it. With the vendor filter on, that request is a plausible candidate for Gemini blocking the grading call itself — which would have become a `ProviderError`, a recorded degradation, and no verdict, instead of the clean `block / critical / violence` above. A guard the vendor can prevent from running is not a guard.

---

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Real schema-valid beat via Gemini, no Anthropic/OpenRouter key; `select_provider()` returns `google` | ✅ **live** | `select_provider() -> google`, `model_label -> google:gemini-2.5-flash`; `manage.py hello_beat` returned a validated `Beat` ("Pip is sitting by the water… Can you count the frogs with me?") with both keys blanked. |
| 2 | The real beat schema round-trips the sanitizer into a valid `Beat` | ✅ **live + tested** | The live beat above *is* the round-trip. Offline: the real contract validates against `google.genai.types.Schema` (which forbids extra keys), as do `StoryPlan`, `SessionMemories` and `SafetyVerdict`; no `$ref`/`$defs`/`additionalProperties` anywhere in the rendered output; the required-but-nullable trio keeps both halves. |
| 3 | A Gemini safety block surfaces as `ProviderError` and lands in the fallback-beat path | ✅ tested | Six refusal shapes → `ProviderError` (prompt block, no candidates, SAFETY, PROHIBITED_CONTENT, MAX_TOKENS, empty text); an SDK `ClientError` → `ProviderError` with its `__cause__` kept. End to end: a SAFETY-blocked provider driven through `orchestrator.create_beat` yields a warm playable fallback that passes `policy.check_text` and is honestly tagged `fallback:`. **Not seen from a live Gemini refusal** — see below. |
| 4 | `last_usage` populated from `usage_metadata`; a metered-then-failed generation is still costed | ✅ **live + tested** | Live, on two models and two operations: beat `units={'input_tokens': 986, 'output_tokens': 1136} usd=0.0031358` and, on a second run of the same prompt, `output_tokens: 1572 usd=0.0042258`; safety `units={'input_tokens': 807, 'output_tokens': 45} usd=0.0000987`. All `priced=True`. The beat spread is thinking, which Gemini sizes per request — budget on the upper figure. Tested: thinking tokens folded into output; a SAFETY-blocked call still reports `{input: 900, output: 0}`; a call that never reached the model reports `None`. |
| 5 | The image gate runs under Google with no Anthropic key; benign passes, unsafe blocked, failure fails closed | ✅ **live + tested** (one gap) | Live: `select_moderator() -> GeminiImageModerator`, `media.scan … moderation=passed` — a real vision call, not the structural fallback. Tested: UNSAFE→`BLOCKED` with reason, provider exception→`FLAGGED` fail-closed, provider refusal→`BLOCKED`, empty answer→`ModerationUnavailable`, `thinking_budget=0` asserted. **The live pass was a 1×1 PNG, not real generated art, and no live image has been *blocked*.** |
| 6 | Boot + preflight guards | ✅ **live + tested** | Live: the `google` branch of `pairing_error` refused the founder's own `.env` over a leftover `google/gemma-4-26b-a4b-it` in `AI_MEMORY_MODEL` (see finding 2b). Tested: `prod.py` refuses each of the three missing settings and a credentials path that is not a file; `IMAGE_MODERATION_PROVIDER=google` alone triggers it; `google` further down the order does not. Preflight on the all-Google config: `privacy.provider_terms` OK, `grader_is_separate` OK, `grader_reachable` OK, `spend.pricing` OK, exit 0 — and FAIL when the credentials are removed. |
| 7 | `pip-audit` clean with `google-genai`; the CI `Dependencies` job green | ✅ | `pip-audit -r backend/requirements.txt` → *No known vulnerabilities found*. CI run below. |
| 8 | `make lint && make test && make evals` green; `mypy` clean; docs updated | ✅ | **1450** backend + **183** frontend + **44 passed / 1 skipped** evals; ruff clean both sides; `mypy` 0 issues in 262 source files; no missing migrations; the committed OpenAPI schema is byte-identical (nothing here touches a serializer or a route). No document says `AI_PROVIDER_ORDER=gemini` any more except the two that warn against it. |

## Self-review against `Docs/Review/Vertex-Gemini.md`

Its eight press-points, answered. This is the build's own account and not a substitute for the audit.

1. **Gemini schema rejection.** The real beat schema — not a toy — validates against `types.Schema`, which forbids extra keys, so an unlisted keyword fails rather than passing quietly. `StoryPlan`, `SessionMemories` and `SafetyVerdict` too. A live beat and two live safety grades round-tripped it. The `Beat` model, its `extra="forbid"` and `ModelRouter._parse` are untouched, and a test asserts both halves of that.
2. **Safety-block handling.** Filters `OFF`, our grader the authority — and the live grader run above is the concrete argument for it. All six refusal shapes become `ProviderError`; a stubbed block driven through `orchestrator.create_beat` yields a warm playable fallback that passes `policy.check_text`. **Not yet seen from a live Gemini refusal.**
3. **The image gate under Google.** Live: `select_moderator()` returns `GeminiImageModerator` with no Anthropic key, and `media.safety.scan` logged `moderation=passed` — a vision call, not the fallback. Fail-closed on provider failure is tested; `prod.py` still refuses `IMAGE_MODERATION=off`. **The live pass was a test PNG; no live block, and no real generated art.**
4. **Cost metering.** Read before every raise, live on two models and two operations, thinking tokens included, `priced=True`.
5. **§13.8.** `privacy.provider_terms` OK on the bare Gemini ids; `training_error` still fires on a `:free` route; `grader_is_separate` holds. The exclusion is *stated as a purchasing fact* in `Hosting.md §3` and in `vertex.py`'s docstring, and it is enforced in the one shape code can enforce: the client is only ever built `vertexai=True` with a service account, and there is no Gemini API-key setting anywhere in `settings/base.py`.
6. **Auth and boot gating.** Covered above. `is_configured()` returns `False` and never raises; it opens no socket and stats no file. No key or JSON path is logged — `prod.py`'s boot error names the *path* it could not find, which the operator needs and which is not a secret.
7. **Dependency hygiene.** `google-genai==2.8.0` pinned, `pip-audit` clean, CI `Dependencies` green, suite and evals green after the new dep.
8. **No regression in what the audit proved clean.** Full suite green (1450), including the cross-account isolation, deletion-completeness, ceiling, fallback, budget-breaker and ship-gate tests. `AnthropicProvider`, `OpenRouterProvider` and `ClaudeImageModerator` are all still present and still selected by the same configuration as before — `auto` moderation on an Anthropic-only box picks Claude, and a test says so.

**None of the five automatic-P0 conditions fired.** The one that came closest is not in the code: a service-account key was sitting in the repository tree, untracked and un-ignored (finding 2). It was never committed, and it is now outside the tree — but "no secret committed" was true by luck rather than by a rule, until this commit.

## What a live credential could not settle

The founder's key proved the **happy paths** — auth, region, structured beats, the meter, the image gate. Three things remain for a real-key session, and the review brief is right to ask about each:

1. **A genuine Gemini refusal has never been observed.** Every block in criterion 3 is a stubbed response built from the real SDK types. What is proven is that *our* translation of a block is correct; what is not is that Gemini's real block looks like the shape we translate. With the vendor filters off, the remaining triggers are `PROHIBITED_CONTENT` and `RECITATION`, both hard to provoke deliberately with children's-story prompts.
2. **The image gate has never *blocked* a live picture, and has never seen real generated art.** A 1×1 PNG passed. The `--live` eval pass with real Cloudflare illustrations is where this closes; `Docs/Test/test-prerequisites.md` names it as a still-owed item.
3. **Nothing has run a full sitting on Vertex.** What has run live is one beat, two safety grades, one image scan — three of the four call types, one call each. Sustained behaviour needs `make evals-live` and the Day-10/11 manual test: latency under `MEDIA_TURN_BUDGET_S`, real cost per session, and whether beats ever truncate at 8,000 tokens with dynamic thinking on (the one beat measured used 1,136 output tokens including thoughts, so there is roughly 7× headroom — but a long beat with a hard prompt is the case that would find the edge). The **memory digest** is the one call type not yet exercised live at all.

Also outstanding, and not a code matter: **rotate the service-account key** (it was in a synced folder), and set the GCP budget alerts `Hosting.md §5` calls for before a family plays.
