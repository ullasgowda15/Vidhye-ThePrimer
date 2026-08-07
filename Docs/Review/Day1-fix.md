# Review — Day 1 FIX (scoped): P1 fixes + OpenRouter provider

> **Session type:** REVIEW (scoped). You are an **independent, skeptical auditor** — you did not build this. This is **not** a full re-audit of Day 1; audit **only the fix diff**. Gather real evidence, then write `docs/Review/reports/Day1-fix-report.md`. **Hard rule: DO NOT modify application code, migrations, config, or tests — only write your report.**
>
> **Inputs:** the repo at its current commit · `docs/Build/Day1-fix.md` (the brief that was executed) · `docs/Review/reports/Day1-report.md` (the original audit + the two P1s) · `Docs/Day1-2ndBuildSessionReport.txt` (the builder's self-report — **cross-check its claims against reality; do not take them on trust**). **Source of truth:** PRODUCT.md §19.3, §19.6, §8.1.

## 1. Why this scope
The original Day-1 audit was thorough and returned no P0s. The fix pass touched a **narrow, high-risk surface**: the structured-output schema, the model router, a new OpenRouter provider, and a few P2s. The last real bug hid in exactly this area *and the builder's own tests missed it*, so audit the **changed lines**, not the whole project. Confirm the fix is real and introduced no regression or scope creep.

## 2. Establish the diff first
```bash
git log --oneline -5
git diff e3a2b21..HEAD --stat          # e3a2b21 was the reviewed Day-1 commit; HEAD is the fix
git diff e3a2b21..HEAD -- backend/apps/story/ai/ backend/config/settings/ .env.example .github/
```
Read the actual changes before judging. Everything below must be checked against the diff + real command output, not the self-report's narration.

## 3. Checklist (audit only the fix scope)
**P1-A — schema can't 400:**
1. `beat_json_schema()` (or equivalent) **recursively** strips every keyword outside the structured-output subset — `minimum`, `maximum`, `multipleOf`, `exclusiveMinimum`, `exclusiveMaximum`, and string/array length constraints — at **all** nesting depths (print the emitted schema and grep it for those keys → none).
2. The returned `Beat` is **still** validated to `difficulty ∈ [0,1]` (the `ge/le` stayed on the Pydantic model). Prove a returned `difficulty=1.5` is rejected.
3. The regression test asserts on the **dict itself, recursively**, and runs with **no live call**; the paired "canary" test (raw Pydantic still emits `minimum`) exists and is meaningful. Run them.

**OpenRouter provider:**
4. Registered in the router alongside the existing providers; base URL `https://openrouter.ai/api/v1`; key from `OPENROUTER_API_KEY`; model from `AI_BEAT_MODEL`; attribution headers set.
5. **Degradation logic is correct:** tries strict `json_schema`, and on the provider's `BadRequestError` degrades to `json_object` with the schema inlined — and **remembers the downgrade** so the repair attempt doesn't re-issue (and re-pay for) a request it already knows will 400. Read this path carefully; it's the residual-risk hotspot.
6. **Anthropic remains the configured primary** and switching `AI_PROVIDER_ORDER` back to `anthropic` routes to Anthropic with **no code change** (verify by reading the selection logic; a no-key run should attempt Anthropic and fail cleanly).
7. Tests make **no live provider calls**; the wire-body test (mock transport) genuinely asserts URL + headers + `strict:true` + zero unsupported keywords.

**P1-B — CI + P2s:**
8. CI passes **both** `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY` empty; the Celery step waits on readiness (not a fixed sleep); the `mypy continue-on-error` is called out; README has an "Enable CI" section that DEPLOY links to.
9. `interaction.type` is an enum; `.env.example` documents `OPENROUTER_API_KEY` + `AI_BEAT_MODEL` + `AI_PROVIDER_ORDER`; the `AllowAny` Day-2 TODO is present.

## 4. Cross-cutting checks
- **Scope discipline:** the diff touches only the fix surface — **no Day-2 work** (no auth endpoints, no story loop), no unrelated refactors.
- **Secret hygiene:** no real keys in the diff; `.env` still untracked; `.env.example` valueless.
- **Self-report honesty:** flag any claim in `Day1-2ndBuildSessionReport.txt` that the diff/tests do **not** support.
- **Deviation review:** the builder kept OpenRouter in `router.py` rather than a `providers/` package — confirm that's clean (one convention, not two) and acceptable.
- **No regression:** `make lint && make test` + `makemigrations --check` still green; `frontend/openapi.json` unchanged (staleness gates clean).

## 5. Report to write → `docs/Review/reports/Day1-fix-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 1 FIX Review Report — P1s + OpenRouter". Header (timestamp + `git rev-parse --short HEAD` + reviewer), Overall verdict, Scorecard (criteria 1–9 + counts), What's working, Punch-list (P0/P1/P2), Scope & risk notes, Unverified items.

## 6. Verdict rules
**NOT-READY** if any P0: unsupported keys still reach a provider, `difficulty` range no longer enforced on the returned object, the degradation path is wrong (e.g. re-pays for the rejected request, or never produces valid JSON), Anthropic switch-back requires a code change, a live provider call in tests, or scope creep into Day 2. **FIX-THEN-SHIP** if no P0 but ≥1 P1. **SHIP** only if all in-scope criteria are ✅.
- **`hello_beat` live call is ⚪ Unverified by you** (no key) — that is the founder's manual test and is the intended closer for criterion 2. Say so; do not guess.

## 7. After writing
Print a 5-line summary (verdict, counts, any P0/P1 titles). Do not fix anything; do not start Day 2.
