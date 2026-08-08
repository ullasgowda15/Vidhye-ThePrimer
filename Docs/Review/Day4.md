# Review — Day 4: Story Orchestrator v0 (Plan + First Beat over SSE)

> **Session type:** REVIEW. Independent, skeptical auditor. Gather evidence, then write `docs/Review/reports/Day4-report.md`. **Do NOT modify code — only your report.** Audit against `docs/Build/Day4.md` + PRODUCT.md §8/§16.4/§18.4–18.5/§19.6. Verdicts with evidence.

## 1. Method
Start an adventure and **consume the SSE stream with a streaming client** (`curl -N`), confirming beats arrive incrementally (not one buffered blob — that would mean WSGI/misconfigured ASGI). Validate the beat JSON against the §8.1 schema. Force a malformed generation (mock/inject) to prove the repair path. Watch the frontend network panel for any spinner.

```bash
ADV=$(curl -sX POST :8000/api/v1/children/<id>/adventures -H "Authorization: Bearer $ACCESS" | jq -r .adventureId)
curl -N :8000/api/v1/adventures/$ADV/stream        # MUST stream incrementally, not buffer
# check response headers: Content-Type: text/event-stream ; not fully buffered
cd backend && pytest apps/story -q
```

## 2. Checklist (1:1 with Day 4 acceptance criteria)
1. `POST /adventures` persists an `Adventure` + `plan`; returns a valid `streamUrl`.
2. SSE streams a **schema‑valid** beat 1 (validate every required §8.1 field) then `done`; genuinely incremental under ASGI (header + timing evidence).
3. Beat 1 is **personalized** from the child's seeds (quote the beat; show the seed link).
4. Illustration + TTS generated **in parallel** (log timings proving overlap, not sequential); URLs attach to the beat.
5. `StoryPage` renders narration first, swaps media in; **no spinner** ever (companion idle covers the gap).
6. Repair path fires on malformed output (force it); persistent failure ends the stream gracefully, not a crash.
7. lint/test/CI green; an SSE test asserts a valid streamed beat.
8. **Session survives a reload (§4.6):** verify the *written clause* (refresh cookie `HttpOnly`+`SameSite=Lax`, `access` only in the body, no login-screen flash, parent gate NOT restored) **and** the *founder test* (a hard refresh mid-story returns the child to **the same** adventure — not a freshly generated one). These can diverge: the cookie can be perfect while the client still POSTs a new adventure on reload. Check both, and check the DB for duplicate adventures/opening beats per child as the tell.
9. **Dev CORS is scoped (§4.6):** a preflight from `https://evil.example` gets **no** `access-control-allow-origin`; `http://localhost:5173` is allowed with credentials. Also check a **simple** (non-preflight) `GET`/`POST` carrying the evil `Origin` — a preflight-only test misses it. Confirm `CORS_ALLOW_ALL_ORIGINS` is gone from `dev.py`.

## 3. Cross-cutting checks
- **Streaming correctness (§16.4):** is the response truly chunked? Inspect headers + observe incremental arrival. This is the #1 thing to get wrong — buffering is P0.
- **Latency‑hiding (§4.9):** the "no spinner" rule — is loading dressed as the companion thinking, or is there a hidden spinner/blank flash?
- **Structured‑output discipline (§19.6):** validation + a single repair, not infinite retries; malformed output can't reach the child.
- **Scope discipline:** no choices/consequence loop, memory, or safety hardening front‑run.

## 4. Report to write → `docs/Review/reports/Day4-report.md`
Same structure as `reports/Day1-report.md`, titled "Day 4 Review Report — Story Orchestrator v0". Paste a sample streamed beat JSON and the parallel‑timing log as evidence.

## 5. Verdict rules
**NOT‑READY** if any P0: stream buffers instead of streaming, beat invalid against schema, spinner shown to child, malformed output reaches the UI, refresh token readable by JS, CI red. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 9 ✅ (a written clause passing while its founder test fails is a 🟡, not a ✅).

## 6. After writing
Print a 5‑line summary. Do not fix; do not proceed to Day 5.
