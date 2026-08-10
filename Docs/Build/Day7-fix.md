# Build — Day 7 FIX: transport-error safety, escalation ingress, injection hardening

> **Session type:** BUILD (fix pass for Day 7). Read fully, fix everything in scope, run the **Self-review** in §7, then **Commit & push** in §8. Do **not** start Day 8. **This is the safety day — a green suite is NOT sufficient; prove each fix by attacking it, as the review did.**
>
> **Inputs:** this file · `docs/Build/Day7.md` · `docs/Review/reports/Day7-report.md` (probe/line refs below are from it). **Source of truth:** PRODUCT.md §13.2 (defense-in-depth), §13.10 (escalation), §19.6 (unrepairable → safe fallback), §4.9 (never a spinner).
>
> **Why:** Day 7 reviewed **NOT-READY** with two P0s. Both fail closed (no unsafe content reached a child), but both break a core promise — a provider error strands the child on the thinking companion, and a child's disclosure is dropped on 3 of 4 paths.

## 1. P0-1 — a provider transport error must become a safe fallback, never a dead stream
`openai`/`anthropic` SDK exceptions are **not** `ai_router.AIError` subclasses, so a timeout/429/5xx/**400** blows through `create_beat`'s `except AIError` (`orchestrator.py:749`), through `guard_output`'s `except ClassifierUnavailable` (`pipeline.py:314`), and through `stream_beat`'s `except AIError` (`streaming.py:234`) — the SSE generator dies after `: open` and `StoryPage` (no error state by design) shows the companion thinking **forever**. Hit live: the founder's `.env` 400s every classifier call.

**Do:**
- **Wrap at the adapter boundary.** In both `AnthropicProvider.generate_json` (`router.py:126-164`) and `OpenRouterProvider.generate_json` (`:235-288`), put every provider call in `try/except Exception → raise ProviderError(...) from e` (a new `AIError` subclass). The AI layer must **only ever** emit `AIError` subclasses — no raw SDK exception escapes.
- **Classifier degrades on any error.** `classifier.classify` must catch `Exception` (not just `AIError`) and raise `ClassifierUnavailable`, so `guard_output` returns `degraded=True` + writes a `classifier_unavailable` incident instead of raising.
- **Tests (attack, don't assert-only):** a provider raising a transport error at (a) `build_plan`/`create_beat` → a **persisted warm fallback beat + incident**, stream ends with a playable beat not a crash; (b) the classifier → `guard_output` returns `degraded=True` + incident, deterministic verdict stands. Probe L/M/A are the shapes.

## 2. P0-2 — check a disclosure where it arrives, not when the next beat generates
`guard_input` sees the child's words only via `guard_beat_inputs` inside `create_beat` (`orchestrator.py:589`). The endpoint that **receives** them — `AdventureChoiceView.post` → `record_choice` (`views.py:272`, `orchestrator.py:1386`) — has no guard call, so a disclosure in `Choice.value` is dropped on: the **close** (`ADVANCE_FINAL`), the child **leaving** (`advance_adventure=skipped`), and a **prefetched** next beat (`ADVANCE_READY`) — probes N/O/Q, 0 incidents each.

**Do:**
- Run the distress/disclosure check **in `record_choice` (or the view) on `value`/`text`, before the 202 returns** — independent of whether a next beat is ever generated.
- Keep `guard_input`'s check as the defense-in-depth second pass, with dedupe so one disclosure still files exactly **one** incident + one parent-notify.
- **Tests:** each of the three holes (close / leave / prefetched) now files a `child_distress` critical incident + parent-notify; the mid-story control still files exactly one.

> Practical exposure is limited today (the UI sends only tapped keys), which is *why* to fix it now — Week 2 opens free text + voice, and an escalation path added after the surface that produces escalations has never run.

## 3. P1-1 — injection: normalise, and record what's still open
Four novel attacks reached the instruction layer verbatim (Spanish, Greek-iota homoglyph, base64, bare imperative) — the classifier that would catch them is the one that can't run.
**Do:** (a) **NFKC-normalise + strip zero-width/homoglyph chars** in `scrub` before matching (closes homoglyph + fullwidth for one function); (b) add the non-English + bare-imperative families to `corpus.py` as **known-open** entries with a marker so the gap is recorded, not assumed closed; (c) restate criterion 3 / the Day-7 end state as *"every recorded injection shape is neutralised; the classifier is the recall layer"* — honest once P0-1 + P1-2 let it run.

## 4. P1-2 — make the classifier actually run (validate the model/provider pairing)
`.env` leads `AI_PROVIDER_ORDER` with `openrouter` but leaves `AI_SAFETY_MODEL` at its Anthropic default → `400 not a valid model ID` every beat; with P0-1 fixed this becomes a degradation storm (strongest guard silently off).
**Do:** validate the pairing at boot the way `prod.py` validates flags — if the lead provider is `openrouter` and `AI_SAFETY_MODEL` isn't a valid slug, **fail loudly**; add `AI_SAFETY_MODEL=<vendor/model>` (a **capable** model, not a weak one) to the OpenRouter block in `.env.example` with a comment that the safety grader must not be a throwaway model.

## 5. P1-3 — the fallback close must not promise something that wasn't in it
`safe_fallback_beat`'s `is_final` branch appends a fixed *"come see that little snail"* hook to **any** template (`fallbacks.py:278`), so most fallback closes promise a thing the child never saw — the Day-5 misattribution class, in the retention beat. **Do:** give each template its own `tomorrow_hook` and use the selected template's; test that the hook names something in that template.

## 6. Fold (cheap, safety-relevant)
- **P2-1 — record `degraded`.** Add a `degraded` member to `Beat.SafetyStatus` (migration) and write it from `decision.degraded`, so a beat the classifier never graded is distinguishable from one both guards passed. Without it "which beats did the strong guard actually approve?" is unanswerable except by correlating incident timestamps.

## 7. Do NOT do / Self-review (REQUIRED)
Out of scope: memory/retrieval (Day 8), the offline eval suite / real recall measurement (§19.7, later), the image-gate live smoke (P2-2, needs a real key), P2-3/P2-4.
Write a short evaluation rating each **Verified/Partial/Unverified** with evidence — and **attack each fix**, don't just run tests:
1. `make lint && make test` + migration clean; CI green after push.
2. Force a provider timeout/400 at beat gen AND at the classifier → persisted warm fallback + incident in the first case, `degraded=True` + incident (no raise) in the second. No dead stream.
3. Disclosure in `Choice.value` on the close, on leave, and on a prefetched beat → one critical incident + parent-notify each.
4. Homoglyph/fullwidth injection now neutralised; the four known-open families are marked in the corpus.
5. Boot fails loudly on a mis-paired `AI_SAFETY_MODEL`; a fallback close's hook names something in its own template.
6. `degraded` status written when the classifier couldn't grade. State what you could not verify (real recall — no live grader).

## 8. Commit & push
`git status` (no secrets/media) → stage fix + migration + `docs/Build/Day7-fix.md` → commit `Day 7 fix: transport-error fallbacks, escalation ingress, injection + classifier hardening` → `git push`, confirm Actions green.

## 9. End state
Every provider error becomes a warm fallback (beat) or a degradation (classifier), never a dead stream; a disclosure is caught where it arrives on every path; injection normalisation closes the homoglyph families and records what's still open; the independent classifier is validated to actually run; and fallback closes don't promise the unseen. Fail-closed everywhere, and now fail-*gracefully* too. Ready for re-review.

---
**Founder note:** to make the independent classifier live, set `AI_SAFETY_MODEL` in `.env` to a **capable** model valid for your OpenRouter gateway (not a weak free one — it's the safety grader). Then run the §8 manual test with a real key and confirm `safety.classifier` log lines show the independent call completing on a live beat — that's the highest-value hour once this fix lands.
