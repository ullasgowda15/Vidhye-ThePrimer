# Build — Day 1 FIX: Close P1s + add OpenRouter provider

> **Session type:** BUILD (fix pass for Day 1). Read this whole file first, then fix everything in scope, self‑review against the acceptance criteria, and stop — do **not** start Day 2.
>
> **Inputs you have:** this file · `docs/Build/Day1.md` (original brief + acceptance criteria) · `docs/Review/reports/Day1-report.md` (the audit that produced this punch‑list). **Source of truth:** `docs/PRODUCT.md` §19.3 (model router / provider portability), §19.6 (structured output + validate‑repair), §8.1 (Beat contract).
>
> **Why this pass exists:** the Day 1 review returned **FIX‑THEN‑SHIP** with **no P0s** but **two P1s**, and both of the review's ⚪ Unverified items (live `hello_beat`, CI green) are blocked by the founder not having an Anthropic API key. We fix the P1s **and** add **OpenRouter** as a provider so the AI path can be verified end‑to‑end on a free model now, with a one‑line switch back to Anthropic later.

---

## 1. Scope of this pass
1. **P1‑A** — fix the `Beat` structured‑output schema so a live call cannot 400 on unsupported JSON‑Schema keys, and add a test that catches this **without** a live call.
2. **P1‑B** — make CI runnable (founder adds the remote; you make sure the workflow is correct and provider‑key‑free in CI).
3. **New** — add an **OpenRouter** provider to the model router, fully **env‑driven**, so `AI_PROVIDER_ORDER=openrouter,anthropic` uses OpenRouter in dev and reverting to `anthropic` uses Anthropic with **no code change**.
4. Sweep the cheap **P2s** the review flagged that are one‑liners (see §5).

## 2. P1‑A — Beat schema / structured outputs (`backend/apps/story/ai/`)
- In `schemas.py`, the `Beat` field `difficulty` currently uses `Field(ge=0.0, le=1.0)`, which Pydantic emits as `"minimum"/"maximum"` — **not** in the structured‑outputs JSON‑Schema subset. `router.py` passes the **raw** schema dict to the provider, so nothing strips them.
- **Fix:** stop sending numeric constraints to the provider. Either (a) drop `ge`/`le` from the model and enforce the 0–1 range as a **post‑validation** check on the returned `Beat`, or (b) keep the constraint on the Pydantic model but **pop** `minimum`/`maximum`/`multipleOf`/`exclusiveMinimum`/`exclusiveMaximum` from the emitted JSON Schema in `beat_json_schema()` before it reaches the provider. Prefer (b) so validation of the *returned* object still enforces 0–1.
- **Regression test (no live call):** add a test asserting the schema actually sent to the provider contains **none** of the unsupported keys, recursively. This must run in CI (which has no key), so it cannot rely on a real request. The existing `test_hello_beat_command.py` monkeypatches `generate_json`, so it never sees the real request shape — this new test closes that gap.

## 3. New — OpenRouter provider (env‑driven, no lock‑in)
OpenRouter is **OpenAI‑API‑compatible**, so add it as an OpenAI‑style adapter — do **not** rewrite the router.
- **Adapter:** `apps/story/ai/providers/openrouter.py` (or extend the existing OpenAI seam). Base URL `https://openrouter.ai/api/v1`; key from **`OPENROUTER_API_KEY`**; optional headers `HTTP-Referer` and `X-Title` (OpenRouter attribution — safe to set to the app name). Use the `openai` Python SDK pointed at that base URL.
- **Register in the router** as a selectable provider. Provider order and model come from env (already read in `base.py`): `AI_PROVIDER_ORDER` (e.g. `openrouter,anthropic`) and `AI_BEAT_MODEL`. **Anthropic stays the configured primary**; dev just overrides via `.env`.
- **Structured output — degrade gracefully (§19.6):** many free OpenRouter models don't support strict `json_schema`. Implement: if the selected model/provider supports `response_format: json_schema`, use it; **otherwise** fall back to JSON mode (`response_format: {"type":"json_object"}`) + the existing **validate‑and‑repair** path. Do **not** assume strict schema enforcement on OpenRouter.
- **Model choice is configurable, not hardcoded:** free model availability on OpenRouter changes over time. Read the model from `AI_BEAT_MODEL`; document in `.env.example` that the founder should pick a currently‑available free model that supports JSON/tool output. Do not bake a specific model name into code.
- **Keep CI provider‑free:** CI must continue to pass `ANTHROPIC_API_KEY=''` **and** `OPENROUTER_API_KEY=''` and never call a live provider — tests stay mocked.

## 4. P1‑B — CI can actually run
- The workflow logic was reviewed as sound; it has simply never run (no git remote). **This step needs the founder** (GitHub auth). Your job: make sure the workflow is correct with the new env var present‑but‑empty, and add a short **"Enable CI"** section to `README.md`/`DEPLOY.md`: create the GitHub repo, `git remote add origin …`, `git push`, confirm the run is green.
- While here, address the review's two CI watch‑items: the `--detach`ed Celery worker with a fixed `sleep 10` (make it wait‑for‑ready or bump/guard it so a slow runner isn't flaky), and note that `mypy` is `continue-on-error: true` (leave it, but call it out).

## 5. Cheap P2s to fold in (one‑liners)
- `interaction.type`: change from free `str` to an **enum** (`count`/`choose`/`say`/`tap`/`none`) — `enum` **is** supported by structured outputs and lets the provider constrain generation. Update the Day‑4 dispatch expectations accordingly (comment only).
- `.env.example`: add **`OPENROUTER_API_KEY`**, **`AI_BEAT_MODEL`**, **`AI_PROVIDER_ORDER`** (the last two are read by `base.py` but missing from the example).
- Leave `DEFAULT_PERMISSION_CLASSES = AllowAny` **as‑is today** but add a `# TODO(Day 2): flip to IsAuthenticated + @permission_classes([AllowAny]) on health` note so it doesn't silently outlive its justification.

## 6. Do NOT do today
- ❌ Don't start Day 2 (no auth endpoints, no story loop). ❌ Don't remove Anthropic — it stays the configured primary; OpenRouter is an added, env‑selected option. ❌ Don't commit any real key (`.env` only; `.env.example` stays valueless). ❌ Don't rely on strict `json_schema` for OpenRouter free models — use the JSON‑mode + repair fallback. ❌ Don't wire images/TTS to OpenRouter — it's LLM‑only; those come Day 3 with their own providers.

## 7. Acceptance criteria (done when ALL true)
1. The schema sent to the provider contains **no** `minimum`/`maximum`/`multipleOf`/`exclusive*` keys (new recursive test proves it); the returned `Beat` is still validated to `difficulty ∈ [0,1]`.
2. With `AI_PROVIDER_ORDER=openrouter,anthropic`, a real `OPENROUTER_API_KEY`, and a valid free `AI_BEAT_MODEL`, `python manage.py hello_beat` returns a **schema‑valid** Beat (JSON‑mode + repair path works if the model lacks strict schema).
3. Setting `AI_PROVIDER_ORDER=anthropic` (with an Anthropic key) routes to Anthropic with **no code change** — verified by inspection/log even if no Anthropic key is available.
4. `make lint && make test` green; tests make **no** live provider calls; CI still passes with both provider keys empty.
5. `.env.example` documents `OPENROUTER_API_KEY`, `AI_BEAT_MODEL`, `AI_PROVIDER_ORDER`; README/DEPLOY has an "Enable CI" section.
6. `interaction.type` is an enum; the `AllowAny` TODO note is in place.
7. Working tree committed with a clear message (e.g. `Day 1 fix: structured-output schema + OpenRouter provider`).

## 8. Founder manual steps (Appu — outside the build session)
1. Create a free **OpenRouter** account, generate an API key, and pick a currently‑available **free** model that supports JSON/tool output; put both in `.env` (`OPENROUTER_API_KEY=…`, `AI_BEAT_MODEL=…`, `AI_PROVIDER_ORDER=openrouter,anthropic`).
2. Run `python manage.py hello_beat` → confirm a valid frog beat prints.
3. Create the GitHub repo, `git remote add origin …`, `git push`, and confirm the CI run is **green**. That closes the last two ⚪ Unverified items and Day 1 is truly done.

## 9. End state
Day 1's two P1s are closed (schema safe + regression‑tested; CI runnable), OpenRouter is an env‑selectable provider so the whole AI path is verifiable on a free model **and** revertible to Anthropic with a one‑line `.env` change, and the cheap P2s are folded in. Re‑run `docs/Review/Day1.md` if you want an independent re‑audit before starting Day 2.
