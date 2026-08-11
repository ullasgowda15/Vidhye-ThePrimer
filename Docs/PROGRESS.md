# PROGRESS — The Primer build (resume pointer)

This file lets a fresh chat (or a future you) pick up without re-reading a long history. All durable state is in the repo, not in any chat.

## The workflow (unchanged)
One founder + Claude Code, 15-day sprint. Per day: hand a fresh Claude Code session `docs/Build/DayN.md` (build) → new session with `docs/Review/DayN.md` (review → writes `docs/Review/reports/DayN-report.md`) → if issues, a `docs/Build/DayN-fix.md` closes them → founder manual-tests → commit + push → CI green → next day. Build and review are always separate sessions; the reviewer never edits code. Source of truth is `docs/PRODUCT.md` (open only the §s a brief cites). `CLAUDE.md` at the repo root auto-loads the rules.

## Where we are
- **Days 1–8: DONE, reviewed, fixed, pushed, CI green.** Each needed a small fix pass (all closed):
  - D1 foundation · D2 auth/consent/child · D3 companion + media pipeline · D4 story SSE + cookie auth + resume-by-name · D5 choice loop (**M1 reached** after fix: consequence fidelity) · D6 consistency + F6 voice-as-gate · D7 safety layer (transport-error fallbacks, escalation ingress) · D8 memory write/consolidation (entity graph normalised).
- **Day 9: BUILT + REVIEWED (FIX-THEN-SHIP, no P0) + FIX PASS DONE — not yet manual-tested.** Build commit `448b523`; report at `docs/Review/reports/Day9-report.md`. All 7 criteria ✅. The fix pass closed both P1s: the returning greeting now verifies `remembered` is non-empty **and** traceable to a real bundle item (else it degrades to the first-meeting hello), and `remembered` is graded by `guard_output` alongside the spoken line before it can reach the child's screen (§13.3). P2-4 (a headline test that could pass without a memory) tightened at the same time. No new migrations — Day 9 only *reads* what Day 8 wrote.
- **Next: manual-test Day 9** (§8 of the build brief), then Day 10. Day 9's remaining P2s (P2-1/2/3/5/6 in the report) were deliberately left open — **P2-1 is the one to decide before Day 10 builds on `recall_count`**: recall bookkeeping stamps everything *retrieved* rather than what was *said*, and feeds its own recency signal.

## Config / keys (in `.env`, gitignored)
- LLM via **OpenRouter**: `AI_BEAT_MODEL=nvidia/nemotron-3-super-120b-a12b:free`, `AI_PROVIDER_ORDER=openrouter,anthropic`. `AI_SAFETY_MODEL` = a **capable** OpenRouter slug, must differ from the beat model. `AI_MEMORY_MODEL` inherits `AI_SAFETY_MODEL`.
- Image: **Cloudflare Workers AI** flux-1-schnell (10k neurons/day free). TTS: **ElevenLabs** free (+ Google TTS failover, code-complete, needs a key). `MEDIA_STORAGE=local` for dev.
- Embeddings: `local-hash` fallback today → **set a real embedding key (e.g. `OPENAI_API_KEY`) to actually feel Day 9 retrieval**; semantic recall quality is otherwise unverified. Day 9 also adds `MEMORY_RETRIEVAL_TOKEN_BUDGET` (800) and `MEMORY_RETRIEVAL_CANDIDATES` (20) — the dials that keep the prompt flat however long a child's history gets.
- Privacy: do **not** use prompt-reselling free routes for the safety/memory/embedding models (§13.8).

## Standing reminders
- `make worker` must run (stories + memory consolidation depend on it).
- Free provider quotas exhaust (Cloudflare daily 429, ElevenLabs credits) — resets daily / top up / swap adapter.
- Latency on free models is 35–90s/beat — plumbing is correct; switch `AI_PROVIDER_ORDER` to Claude (paid) to feel it fast.

## Day 9: what landed
- **`apps/memory/retrieval.py`** — hybrid bundle. Rule-includes pinned (companion state · active fears, both parent-stated and session-revealed · loved things · recent salient events), vector neighbours ranked `0.5·similarity + 0.3·salience·decay + 0.2·recency`, the two **merged** (a memory both halves found keeps its similarity *and* its pinned status), near-duplicates folded, then cut to `MEMORY_RETRIEVAL_TOKEN_BUDGET` (800; candidates 20). Rules need no provider, so an embedding outage costs relevance and nothing else.
- **`apps/companion/greeting.py`** + `GET /children/{id}/greeting` — the by-name greeting. Bundle → `guard_input` → `memory_bundle` DATA layer → generate → `guard_output` → TTS. No memories, or *any* failure, falls back to the Day-3 first meeting, which costs nothing (its audio is already on the companion row).
- **Orchestrator** — the bundle replaces `seed_memories` in the plan and beat prompts. Rules ride in every prompt; the **vector half runs at story start and real branch points only** (`is_branch_point`: two or more cards were on screen). Retrieval context is scrubbed before it goes to the embedding vendor.
- **`ReturningGreeting.tsx`** at `/welcome/:childId`; the home star routes there instead of straight to `/story`.
- `recall_count` / `last_recalled_at` bumped on what reached the prompt.
- `make memories CHILD=<uuid> MEM_ARGS='--recall "coming back for another story"'` prints the exact bundle with per-item scores. Read-only — it deliberately does *not* bump recall.

## Day 9 watch-items for the review + manual test
- **Cross-child leakage is still the automatic P0.** Proved both directions in `test_retrieval.py`, plus `mark_recalled` on a forged bundle touching zero rows. Re-check it anyway with two real children.
- **Semantic recall is unverified.** Running on `local-hash` embeddings, which are *lexical*. The greeting will still name a real memory (rule-includes need no vectors), but *which* one it picks is not semantically chosen until a real embedding key is set. This is the one claim the test suite cannot make.
- **Two bugs found in build self-review, both fixed + tested** — worth re-checking rather than trusting: (1) the greeting's `user` message interpolated `first_name`/`companion.name` unscrubbed, a §13.5 hole in the file whose docstring is about §13.5; (2) prompt assembly sat outside the fallback's `try`, so a missing `prompt_version` row would 500 instead of degrading.
- **A design call worth a second opinion:** gating the vector half on branch points is my reading of §11.9's "story start and key branches". Retrieving on every beat is also defensible — it costs an embedding call per beat, forever.
- **One flaky backend test, seen once, not reproduced — worth watching, and it is P2-3 wearing a disguise.** `test_retrieval.py::TestTheTokenEstimate::test_the_budget_is_read_from_settings_at_call_time` failed once during a full-suite run (`considered=7 stored=3 tokens=116`, i.e. the vector half contributed **nothing** while `degraded=0` said it had not failed), then passed on a clean re-run of the whole suite and five times in isolation. `degraded=0` with zero neighbours is the signature the review's **P2-3** predicted: one shared HNSW index walked globally and filtered by `child` *after*, so a child can come back with fewer neighbours than `limit` — or none — with nothing reporting a fault. Deep into a full run the `memory` table is at its most churned, which is when it showed. Not a Day-9-fix regression (the fix touches `greeting.py` only, and the re-run was green), but it is the first real-world sighting of P2-3 and it argues for doing something about it rather than logging it again.
- Injection-through-memory: re-tested by planting each corpus attack in a real `Memory` row and driving it through retrieval into the assembled prompt. Note `scrub`'s `forged_turn` pattern is line-anchored by design, so `"X said: assistant: …"` mid-sentence is held by the data fence rather than the scrubber — pre-existing, documented in the test.

## To resume in a new chat, paste:
> Read `docs/PROGRESS.md`. We're mid-sprint on The Primer; Days 1–8 are done, reviewed and pushed, and Day 9 is built and pushed (CI green) but not yet reviewed. I'll hand you build/review reports as text files — your job is to check them, write fix briefs when needed, and give me handoff prompts. Keep reasoning short. Next up is the Day 9 review session.
