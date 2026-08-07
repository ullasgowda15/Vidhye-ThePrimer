# Build — AI Smoke-Test Harness (automates the Day-1 manual tests)

> **Session type:** BUILD (tooling). Read this whole file first, build it, self-review against the acceptance criteria, then stop — do **not** start Day 2.
>
> **Source of truth:** `docs/PRODUCT.md` §19.3 (router), §19.6 (structured output + validate/repair), §8.1 (Beat). **Context:** `docs/Build/Day1-fix.md` + `docs/Review/reports/Day1-report.md` + `Docs/Day1-2ndBuildSessionReport.txt` describe the current AI layer (`apps/story/ai/`: `schemas.py::beat_json_schema()`, `router.py` with `PROVIDERS`/`generate_json`, the `hello_beat` command, env knobs `AI_PROVIDER_ORDER` / `AI_BEAT_MODEL` / `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY`). Hook into that real code — do not invent parallel plumbing.

## 1. Goal (one sentence)
Replace the human-eyeballed Day-1 AI checks with **one opt-in command, `make smoke` (→ `python manage.py smoke_ai`)**, that makes real provider calls and **asserts** the outcomes, printing a PASS/FAIL summary and a correct exit code — reusable on any future day the AI path changes.

## 2. Why this is a standing tool, not a throwaway
Every day from 4 onward changes prompts, memory injection, or the router. A repeatable live smoke test is how you catch a structured-output/regression bug (exactly the class that hid on Day 1) in seconds instead of by hand. It lives **outside** CI and the default test suite because it costs real tokens and needs a real key.

## 3. Deliverable: `smoke_ai` management command + `make smoke`
`backend/apps/story/management/commands/smoke_ai.py`. Runs these checks in order, each independently PASS/FAIL, and exits non-zero if any fails.

**Preflight**
- Require `OPENROUTER_API_KEY` (or whichever provider is first in `AI_PROVIDER_ORDER`). If missing → clear message naming the env var, exit 2. Never print key values.
- If `AI_BEAT_MODEL` is unset and `--pick-free-model` is passed, auto-select one (see §4).

**Check A — happy path (real call).** Generate a real beat through the active provider. Assert: a `Beat` that **validates against the schema**, `difficulty ∈ [0,1]`, required §8.1 fields present. Log which output mode was used (`json_schema` vs downgraded `json_object`).

**Check B — forced fallback (the residual-risk path).** Force the `json_object` + repair path (see §4 for the switch) and assert a **schema-valid** `Beat` still comes back. This is the path most free models actually hit, so it must be proven, not assumed.

**Check C — Anthropic routing / clean failure.** With `AI_PROVIDER_ORDER=anthropic` and **no** Anthropic key, assert the path fails **cleanly** — the documented no-key error, exit 1, **no traceback**. This proves the router selects Anthropic on demand with no code change. (No Anthropic key required — we're asserting the clean failure, which also validates switch-back routing.)

**Summary.** Print a table: `A PASS/FAIL (mode=…)`, `B PASS/FAIL`, `C PASS/FAIL`, then `OVERALL PASS/FAIL`. Exit 0 only if all pass. Idempotent and safe to re-run.

## 4. Two small enablers (clean, not hacks)
- **Forced output mode:** add a router setting `AI_STRUCTURED_OUTPUT_MODE = auto | strict | json_object` (default `auto` = today's behaviour: try strict, degrade on `BadRequestError`). `json_object` forces the fallback. This doubles as a useful prod knob and is what Check B flips. Respect it in the router; don't special-case the smoke command.
- **`--pick-free-model` flag:** query OpenRouter `GET https://openrouter.ai/api/v1/models`, filter to entries whose prompt+completion price are `0` (free), prefer ones advertising structured-output/tool support, and use the first. Print the chosen slug. Keeps the founder from hand-copying a model id. (If the call fails, fall back to erroring with a clear "set AI_BEAT_MODEL" message.)

## 5. Wiring
- `Makefile`: add `smoke: ## run live AI smoke test (needs OPENROUTER_API_KEY)` → `cd backend && python manage.py smoke_ai`.
- `README.md`: a short "Smoke-testing the AI path" section — set the key + model, run `make smoke`, what each check means, and that it costs real (free-tier) calls so it's **not** in CI.
- `.env.example`: document `AI_STRUCTURED_OUTPUT_MODE` (default `auto`).

## 6. Do NOT do today
- ❌ Do **NOT** add `smoke_ai` to `make test`, `pytest` defaults, or CI — it must never run live calls in CI (keeps the Day-1-fix guarantee intact). ❌ No Day-2 work. ❌ Don't log or echo any key material. ❌ Don't fork the router's generation logic — the smoke test calls the **same** `generate_json`/router path the product uses, only with asserts around it.

## 7. Acceptance criteria (done when ALL true)
1. `make smoke` with a valid `OPENROUTER_API_KEY` + free `AI_BEAT_MODEL` runs Checks A–C and prints a PASS/FAIL summary with a correct exit code.
2. **Check A** asserts a schema-valid `Beat` from a real call and logs the output mode used.
3. **Check B** forces `json_object` via `AI_STRUCTURED_OUTPUT_MODE=json_object` and still gets a schema-valid `Beat` through the repair path.
4. **Check C** (provider order `anthropic`, no key) asserts a clean no-key failure, exit 1, no traceback.
5. `AI_STRUCTURED_OUTPUT_MODE` is honoured by the **router** (auto/strict/json_object), default `auto` = unchanged behaviour; covered by a unit test (mocked, no live call).
6. `--pick-free-model` selects a currently-available free model from the OpenRouter models API when `AI_BEAT_MODEL` is unset.
7. `smoke_ai` is **absent** from CI and the default test suite; `make test` still makes zero live calls; `make lint && make test` green.
8. README + `.env.example` updated; committed with a clear message.

## 8. Founder use (after this is built)
Once your `OPENROUTER_API_KEY` is in `.env`: `make smoke`. It performs Tests 1–3 of the manual list automatically and tells you PASS/FAIL. The only remaining by-hand steps are the one-time OpenRouter key creation and the one-time GitHub repo + push for CI (Test 5) — everything else is now a single command you can re-run any day.

## 9. End state
An opt-in `make smoke` harness asserts the live AI path (happy path, forced fallback, Anthropic routing) with a clear PASS/FAIL and correct exit code, backed by a real router `AI_STRUCTURED_OUTPUT_MODE` knob and optional free-model auto-pick — kept entirely out of CI. The Day-1 manual AI tests are now automated and reusable for every future day.
