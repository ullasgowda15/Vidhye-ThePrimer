# Build — Day 4 FIX: Resume-by-name, media that survives disconnect, stream throttle

> **Session type:** BUILD (fix pass for Day 4). Read this whole file first, fix everything in scope, run the **Self-review & evaluation** in §7, then do the **Commit & push** in §9. Do **not** start Day 5.
>
> **Inputs:** this file · `docs/Build/Day4.md` (original brief + acceptance criteria) · `docs/Review/reports/Day4-report.md` (the audit that produced this punch-list — file/line hints below are from it). **Source of truth:** `docs/PRODUCT.md` §4.9 (latency-hiding), §16.4 (streaming), §18.4–18.5 (adventure API/SSE), §22.4 (cost).
>
> **Why this pass exists:** Day 4 reviewed **FIX-THEN-SHIP, no P0**, but two P1s share one root cause — **the client cannot resume an adventure by name**, so a reload starts a brand-new story and a disconnect orphans a paid-for beat. Both **multiply on Day 5**, which opens a stream per choice over a long sitting, so they must be closed first.

---

## 1. Scope of this pass
1. **P1-1** — a reload (or any remount) resumes the **same** in-progress adventure instead of generating a new one.
2. **P1-2** — a beat's media finishes **independently of the response lifecycle**, so a client that disconnects mid-generation does not leave a paid-for beat with no picture/voice; and concurrent opens don't double-pay.
3. **P2 fold** — throttle the stream endpoint (same cost-amplification family; StrictMode makes it real in dev).
4. **P2 fold (cheap, Day-5-relevant)** — make the SSE fetch survive an expired access token instead of stranding the child.

**Explicitly OUT of scope (do not touch):** the latency provider-switch to Claude (a config toggle for when a paid key exists — §16.1; the review says *measure on Claude before any latency work*), enforcing the `learning` block (Day 10), and anything in the choice/consequence loop (Day 5). Leave `MAX_OUTPUT_TOKENS` alone.

## 2. P1-1 — Resume an adventure by name
Current state (from the report): `useBeginAdventure` is a `useQuery` keyed `['adventure', childId]`, `staleTime: Infinity` (`queries.ts:194-205`), but React Query's cache is **memory-only** (no `localStorage`/`persistQueryClient` anywhere), so a hard refresh loses the adventure id and `StoryPage` POSTs a fresh adventure. Two POSTs 15s apart returned different adventures; the dev DB holds 7 adventures / 6 opening beats for one child in 23 minutes. The comment at `queries.ts:190-192` claims the backend replay makes reload safe — true of the backend, **false of the product**, because the backend is never asked for the same adventure twice.

**Do:**
- Make **`POST /api/v1/children/{id}/adventures` idempotent w.r.t. an active adventure:** if the child already has an `in_progress` adventure, **return it** (200) instead of creating a second one (201). This alone fixes both the reload and the StrictMode double-POST. A child only gets a *new* adventure once the current one is completed/abandoned (completion arrives Day 5; for now `in_progress` is the gate).
- Add **`GET /api/v1/children/{id}/adventures/current`** → the in-progress adventure + its `streamUrl`, or `204` if none. The child surface calls this on mount and resumes that stream; only if `204` does it POST.
- Frontend: on `StoryPage` mount, resolve the adventure via `current` (or the idempotent POST) **before** opening the stream, so a reload rejoins the same adventure and hits the backend's existing replay path. Delete/replace the memory-only `['adventure', childId]` assumption.
- **Correct the misleading comment** (`queries.ts:190-192`) to describe what actually happens now.

## 3. P1-2 — Media must survive a disconnected client
Current state: a `curl -N` killed after 2s still logged `story.beat … gen_ms=26375` 24s later — the offloaded thread runs to completion and persists the beat, but `stream_media` never ran, so the beat has no image/voice. It self-heals only if the **same** adventure is reopened (`first_beat_state` returns `None` URLs and the media stages run) — which P1-1 now makes possible, but relying on reopen is fragile. Under `StrictMode` the stream effect double-mounts, so a dev page load opens the stream twice and pays for two beat generations, one of which loses the `(adventure, seq)` race and is discarded.

**Do:**
- **Run the media stages as a Celery task keyed on the beat**, enqueued when the beat is persisted — not inside the request coroutine. The media pipeline is already idempotent and cache-first (Day 3), so this is safe and cheap. A vanished client then no longer orphans a beat: the task finishes the image + voice regardless.
- The stream, while connected, **subscribes to / polls for** the beat's media and emits the `media` frames as they land (unchanged UX). On reopen/replay, emit whatever media already exists and let the task finish the rest.
- **Guard first-beat generation against concurrent opens** so two near-simultaneous streams (StrictMode, or two devices) cannot both call the provider: take a short lock (Redis lock or `select_for_update` on the adventure) around "generate beat 1 if absent". The `(adventure, seq)` unique constraint stays as the backstop, but the lock stops the **double-pay** the constraint can't (both callers hit the provider before it fires — the same race noted in the Day-3 fix).
- Replace the implicit `if not (image_url and audio_url)` heal with an **explicit** "beat exists but media incomplete → ensure the media task is running" branch, so a beatless beat is a deliberate repair, not an accident.

## 4. P2 folds
- **Throttle the stream endpoint.** `GET /adventures/{id}/stream` is a plain async Django view, so DRF's `ScopedRateThrottle` never sees it (`views.py:135`); only the POST is throttled. Add throttling to the stream view (reuse the `adventure` scope or a dedicated one) so N concurrent opens on one beatless adventure can't run N generations.
- **SSE 401 survival (cheap, Day-5-relevant).** `consumeBeatStream` is the one authenticated call that bypasses `request()` in `client.ts`, so it gets no silent-refresh-and-retry: an access token that expires before the stream opens leaves the child on a thinking companion forever (`sse.ts:131-137`). Route it through the same refresh-then-retry-once path as other authed calls. (If this proves non-trivial, downgrade it back to a logged P2 rather than expanding scope — say so in the self-review.)

## 5. Do NOT do
- ❌ Don't start Day 5 (no choice endpoint, no consequence beats). ❌ Don't switch providers or touch latency architecture — that's a later config toggle after measuring on Claude. ❌ Don't enforce the `learning` block (Day 10). ❌ Don't weaken the no-spinner / never-an-error child surface, the validate-before-persist ordering, or the `HttpOnly` cookie. ❌ Don't commit `.env` or `backend/media/` (gitignored — keep it so).

## 6. Founder-visible behaviour after the fix
A hard refresh mid-story returns the child to **the same** adventure (same plan, same opening beat), served from replay in well under a second — no new generation, no new spend. Killing a stream mid-generation still results in a beat that ends up with its picture and voice (finished by the task), reachable on the next open. The adventure count in the DB stops growing by one per reload.

## 7. Self-review & evaluation (REQUIRED before you stop)
Do not declare done from unit tests alone. Re-verify against reality and **write a short self-evaluation** in your final message rating each item **Verified / Partial / Unverified** with evidence:
1. `make lint && make test` green; any new migration present and `makemigrations --check` clean.
2. **Reload resumes:** start an adventure, note the id, hard-refresh (or re-`GET current`), confirm the **same** adventureId and the beat arrives via **replay** (sub-second, `story.beat_replayed`, no new `story.beat gen_ms`). Confirm the DB gains **no** new adventure on reload (contrast the Day-4 report's 7-adventures receipt).
3. **Disconnect doesn't orphan:** open the stream, kill it during beat generation, then reopen the same adventure — confirm the beat ends up with both image and audio and there is **no** beatless/mediales orphan left behind. Confirm no `story.beat_race_lost` double-pay when two streams open at once (provider call count == 1).
4. **Throttle:** hammering the stream endpoint returns 429; a normal single open is unaffected.
5. **SSE 401:** (if attempted) an expired access token on stream open triggers a silent refresh and retry, not a permanent thinking companion — or an honest note that it was deferred and why.
6. State plainly what you could **not** verify (e.g. real browser reload — the founder's 30-second devtools job).

## 8. Acceptance criteria (done when ALL true)
1. `POST /adventures` returns the existing `in_progress` adventure for the child instead of creating a second; `GET /adventures/current` returns it (or 204).
2. A reload/remount resumes the **same** adventure and reaches the backend replay path; the misleading `queries.ts` comment is corrected.
3. Media stages run as a beat-keyed Celery task; a client that disconnects mid-generation does not leave a beat without media; reopening emits existing media and finishes the rest.
4. Concurrent first-beat opens call the provider **once** (lock + unique-constraint backstop); no paid-for discarded beat under StrictMode.
5. The stream endpoint is throttled.
6. SSE 401 is survivable (or explicitly deferred with reason in the self-review).
7. `make lint && make test` green; new tests cover resume-on-reload, media-after-disconnect, and single-generation-under-concurrent-open.
8. Self-review & evaluation (§7) written honestly in the final message.

## 9. Commit & push (do this last)
1. `git status` — confirm no `.env`, no `backend/media/…`, no key material is staged.
2. Stage the fix + any migration + the untracked docs (`docs/Build/Day4-fix.md`). `git add -A` is safe given the gitignore; eyeball `git status` first.
3. Commit, e.g. `Day 4 fix: resume-by-name, disconnect-safe media, stream throttle`.
4. `git push`; confirm the GitHub Actions run for the new commit is **green**.

## 10. End state
An in-progress adventure is resumable by name, so a reload rejoins the same story and the backend replay path is finally reachable; beat media is produced by a task that outlives the request, so a disconnected client no longer orphans paid-for beats; concurrent opens generate once; the stream endpoint is throttled. The reload cost-leak and the orphan bleed the Day-4 review flagged are closed before Day 5 multiplies them. Latency (provider switch) and learning-block enforcement remain deliberately out of scope. Ready for an independent re-review, or straight to Day 5 if the founder is satisfied.
