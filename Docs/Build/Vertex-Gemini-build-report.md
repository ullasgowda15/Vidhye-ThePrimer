# Build report — Vertex Gemini provider

> Brief: `Docs/Build/Vertex-Gemini.md`. Reviewer: `Docs/Review/Vertex-Gemini.md`.
> Self-reviewed against the review brief; **not** independently reviewed.

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

**Thinking is left at Gemini's default on the beat path and switched off in the moderator.** The moderator answers one word inside a 100-token budget; a Gemini 2.5 model with dynamic thinking would spend all hundred reasoning and return no text, which fails closed — meaning *every* generated illustration in the product flagged and replaced with stock art, with only a `ModerationUnavailable` in a log to say why. So `thinking_budget=0` there. On the beat path a wrong value is a hard 400 on every generation (models differ in whether they permit 0), and truncation degrades to a fallback beat rather than to a permanently broken gate, so the vendor default stands. It is a real cost knob: the live beat spent 986 in / 1,136 out, and thinking tokens bill at the output rate.

**Gemini's own safety filters are set to `OFF` for the four categories Vertex accepts a threshold for.** This looks like the wrong direction and is the opposite: §13.3 makes our grader the authority, a vendor filter tuned for a general audience has no idea the wolf in a bedtime story is the point, and when it fires we get a blocked response with a category name instead of a graded refusal — a silent availability failure on the surface where the child is waiting. Blocks that happen anyway (unfilterable categories, a policy change) become `ProviderError` and land in the Day-5 degradation path. The image moderator deliberately keeps Gemini's default filters, because there a refusal is a second opinion on exactly the question being asked.

---

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Real schema-valid beat via Gemini, no Anthropic/OpenRouter key; `select_provider()` returns `google` | ✅ **live** | `select_provider() -> google`, `model_label -> google:gemini-2.5-flash`; `manage.py hello_beat` returned a validated `Beat` ("Pip is sitting by the water… Can you count the frogs with me?") with both keys blanked. |
| 2 | The real beat schema round-trips the sanitizer into a valid `Beat` | ✅ **live + tested** | The live beat above *is* the round-trip. Offline: the real contract validates against `google.genai.types.Schema` (which forbids extra keys), as do `StoryPlan`, `SessionMemories` and `SafetyVerdict`; no `$ref`/`$defs`/`additionalProperties` anywhere in the rendered output; the required-but-nullable trio keeps both halves. |
| 3 | A Gemini safety block surfaces as `ProviderError` and lands in the fallback-beat path | ✅ tested | Six refusal shapes → `ProviderError` (prompt block, no candidates, SAFETY, PROHIBITED_CONTENT, MAX_TOKENS, empty text); an SDK `ClientError` → `ProviderError` with its `__cause__` kept. End to end: a SAFETY-blocked provider driven through `orchestrator.create_beat` yields a warm playable fallback that passes `policy.check_text` and is honestly tagged `fallback:`. **Not seen from a live Gemini refusal** — see below. |
| 4 | `last_usage` populated from `usage_metadata`; a metered-then-failed generation is still costed | ✅ **live + tested** | Live: `units={'input_tokens': 986, 'output_tokens': 1136} usd=0.0031358 priced=True`. Tested: thinking tokens folded into output; a SAFETY-blocked call still reports `{input: 900, output: 0}`; a call that never reached the model reports `None`. |
| 5 | The image gate runs under Google with no Anthropic key; benign passes, unsafe blocked, failure fails closed | ✅ **live + tested** (one gap) | Live: `select_moderator() -> GeminiImageModerator`, `media.scan … moderation=passed` — a real vision call, not the structural fallback. Tested: UNSAFE→`BLOCKED` with reason, provider exception→`FLAGGED` fail-closed, provider refusal→`BLOCKED`, empty answer→`ModerationUnavailable`, `thinking_budget=0` asserted. **The live pass was a 1×1 PNG, not real generated art, and no live image has been *blocked*.** |
| 6 | Boot + preflight guards | ✅ tested | `prod.py` refuses each of the three missing settings and a credentials path that is not a file; `IMAGE_MODERATION_PROVIDER=google` alone triggers it; `google` further down the order does not. Preflight on the all-Google config: `privacy.provider_terms` OK, `grader_is_separate` OK, `grader_reachable` OK, `spend.pricing` OK, exit 0 — and FAIL when the credentials are removed. |
| 7 | `pip-audit` clean with `google-genai`; the CI `Dependencies` job green | ✅ | `pip-audit -r backend/requirements.txt` → *No known vulnerabilities found*. CI run below. |
| 8 | `make lint && make test && make evals` green; `mypy` clean; docs updated | ✅ | **1450** backend + **183** frontend + **44 passed / 1 skipped** evals; ruff clean both sides; `mypy` 0 issues in 262 source files; no missing migrations; the committed OpenAPI schema is byte-identical (nothing here touches a serializer or a route). No document says `AI_PROVIDER_ORDER=gemini` any more except the two that warn against it. |

## What a live credential could not settle

The founder's key proved the **happy paths** — auth, region, structured beats, the meter, the image gate. Three things remain for a real-key session, and the review brief is right to ask about each:

1. **A genuine Gemini refusal has never been observed.** Every block in criterion 3 is a stubbed response built from the real SDK types. What is proven is that *our* translation of a block is correct; what is not is that Gemini's real block looks like the shape we translate. With the vendor filters off, the remaining triggers are `PROHIBITED_CONTENT` and `RECITATION`, both hard to provoke deliberately with children's-story prompts.
2. **The image gate has never *blocked* a live picture, and has never seen real generated art.** A 1×1 PNG passed. The `--live` eval pass with real Cloudflare illustrations is where this closes; `Docs/Test/test-prerequisites.md` names it as a still-owed item.
3. **Nothing has run a full sitting on Vertex.** One beat, one plan-less command. Sustained behaviour — latency under `MEDIA_TURN_BUDGET_S`, the grader's verdict quality on Flash-Lite, whether beats truncate at 8,000 tokens with dynamic thinking on, real cost per session — needs `make evals-live` and the Day-10/11 manual test.

Also outstanding, and not a code matter: **rotate the service-account key** (it was in a synced folder), and set the GCP budget alerts `Hosting.md §5` calls for before a family plays.
