# Cleanup — remove dead code, unused deps, stale config, and unused credentials

> **Hand to a fresh Claude Code session (repo root).** Goal: leave the codebase clean and honest now that the deployed stack is **all-Google/Vertex** (beats, safety, memory, image moderation, embeddings) — without breaking the provider-agnostic architecture or anything the Day-1–15 audit proved clean.
>
> **This is a hygiene pass, not a refactor.** Every removal must be *provably* unreferenced or *provably* stale. If you cannot prove it's dead, it stays and goes in the report as "candidate, needs a human."

---

## The one principle that governs the whole pass

**Dead ≠ unused-right-now.** Two different things live in this tree and must be treated differently:

- **Genuinely dead** — unreferenced code, orphaned files, stale config values, unused credentials, commented-out blocks, unused dependencies. **Delete these** (with proof).
- **Intentional seams** — the `AnthropicProvider`, `OpenRouterProvider`, `OpenAIProvider`/`OpenAIEmbeddingProvider`, `local-hash` embeddings, `ClaudeImageModerator`. These are *not* clutter: they are the provider-agnostic router (`PRODUCT.md` D4 — "a routing layer keeps us from vendor lock-in") and the reason the product could swap off a provider that fails or gets expensive. **Do NOT delete these in this pass.** Removing them is an architecture decision (see the gated section at the end), not cleanup.

When in doubt, it stays and you flag it. A wrong deletion of a safety-adjacent or architectural seam is far worse than a leftover.

---

## Ground rules

1. **Green throughout.** `make lint && make test && make evals` must pass after every deletion, and CI green on the final commit. If removing something reddens the suite, it wasn't dead — revert and report it.
2. **Prove it's dead before deleting.** Use tools, not vibes: `ruff --select F401,F811` (unused imports / redefinitions), `vulture backend/` (dead code — install if needed), `deptry backend/` or `pip check` (unused/missing deps), and `grep -rn` across `backend/` + `frontend/src/` + `evals/` to confirm zero references. Record the command and its output for each removal.
3. **Do NOT touch anything the Day-1–15 audit proved clean** (cross-account isolation, deletion, ceilings, fallback, budget breaker, ship gate, the F-1/F-2/F-3 fixes) **unless it is provably dead** — and if it is, quote the proof.
4. **Additive-safe deletions only.** No behaviour change to any live path. Boot guards, `launch_check`, and the safety layer must behave identically.
5. **Report everything**, deleted and kept, in `docs/Build/Cleanup-report.md`.

---

## What to hunt (and delete when proven dead)

### 1. Unused credentials & secrets hygiene
- **`OPENROUTER_API_KEY`** is populated in `.env` and now unused (the deployed provider is `google`). Remove it from `.env`, and **revoke it in the OpenRouter dashboard** (flag this as a founder action — you can't revoke it, only tell them to).
- Any other credential in `.env` for a provider not in the deployed config (old Anthropic key, OpenAI key) → remove from `.env` and list for revocation.
- Confirm no secret is committed or in git history (`git log -p` grep for key prefixes); confirm `.env`, `.env.*`, service-account JSON, and `backups/` are gitignored.
- **Service-account key rotation** is a founder action — restate it in the report: the Vertex SA JSON was in a OneDrive-synced folder and should be rotated.

### 2. Stale config & env
- **`.env` / `.env.example`**: remove variables no longer read by any code path (grep each var name across the codebase; if only `settings/*.py` and nothing consumes it, it's stale). Remove leftover pre-Vertex values (old model ids, `AI_PROVIDER_ORDER=openrouter,...`, `nvidia/nemotron-…`, `google/gemma-…`) from any example/docs.
- Delete stale backup env files (`.env.bak-day8` and similar) after confirming they hold nothing unique.

### 3. Dead code & orphans
- Unreferenced functions, classes, methods, constants (`vulture`), unused imports (`ruff F401`).
- Orphaned modules / management commands / scripts that nothing imports or invokes.
- Orphaned or superseded tests (e.g. a test for a deleted feature). **Do not delete a test just because it's inconvenient** — only if the thing it tests is gone.
- Commented-out code blocks and dead TODOs that reference finished work.
- Superseded docs: e.g. `docs/Test/Day10-manual-test.md` is largely superseded by `docs/Test/manual_testing.md` — **flag for the founder**, don't auto-delete a doc.

### 4. Unused dependencies
- `deptry backend/` (Python) and `npm` unused-dependency check (frontend) → remove packages nothing imports. **Keep `openai`** — it is the OpenRouter client (`OpenRouterProvider` uses the OpenAI SDK), so it is *used*, not dead. Re-run `pip-audit` / `npm audit` after any dependency change; both must stay clean.

### 5. Working-tree hygiene
- `git status`: the reviews have repeatedly noted untracked cruft (`Docs/Day*-Build.txt`, `temp/`, stray transcripts). List them; **do not delete founder-authored files** — flag them for the founder to land or remove.

---

## Deliverable
`docs/Build/Cleanup-report.md`:
1. **Deleted** — each item, the proof it was dead (command + output), the commit.
2. **Kept, and why** — the intentional seams and anything that looked dead but is referenced (name the reference).
3. **Founder actions** — revoke `OPENROUTER_API_KEY`, rotate the SA key, decide on superseded docs / untracked files, and the provider-seam decision below.
4. **Green:** `make lint && make test && make evals`, `mypy`, `pip-audit`/`npm audit` all clean; CI run id.

---

## GATED — do NOT do without explicit founder sign-off in the handoff

**Removing the multi-provider seams entirely** (deleting `AnthropicProvider`, `OpenRouterProvider`, `OpenAIProvider`, `ClaudeImageModerator`, the `openai` SDK dep, and collapsing the router to Google-only).

This is an **architecture change, not cleanup.** It would:
- drop the provider-agnostic router `PRODUCT.md` D4 is built on — the escape hatch that matters *because* Vertex has already shown friction (region 404s, prepay walls);
- delete tested code the ship-gate and boot guards reference;
- require rewriting `PRODUCT.md` §16/§19.3 and several tests (`test_an_anthropic_deployment_is_unchanged`, the pairing-error tests).

**Default: keep them.** Only if the founder's handoff explicitly says "remove the multi-provider seams," do it as its own commit with its own tests and a `PRODUCT.md` architecture update — and even then, keep `local-hash` (offline dev) and the safety fallback paths. Otherwise, propose it in the report and stop.
