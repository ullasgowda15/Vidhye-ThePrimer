# Build — Media Smoke-Test Harness (automates the Day-3 founder test)

> **Session type:** BUILD (tooling). Read this whole file first, build it, self-review against the acceptance criteria, then stop — do **not** start Day 4.
>
> **Source of truth:** `docs/PRODUCT.md` §8.2 (character consistency), §16.6 (illustration service), §13.8 (image safety). **Context:** the Day-3 build (`Docs/Day3-Build.txt`) landed the Cloudflare Workers AI image adapter, the ElevenLabs TTS adapter, the cache-first media service, and a `render_companion` command — but its live verification used **placeholder SVG art**, so the **real providers and real visual consistency are still unproven**. This harness closes that, and is reusable every day the media path changes.

## 1. Goal (one sentence)
Add **`make smoke-media`** (→ `python manage.py smoke_media`) that exercises the **real** Cloudflare + ElevenLabs adapters end-to-end, asserts everything a script *can* judge (real image/audio bytes, correct content-types, cache hits, full reference set), and emits a **contact-sheet HTML** so the one thing a script can't judge — "same character across scenes" and "does the voice feel right" — is a single click for the founder.

## 2. Why a harness (not just the manual test)
The manual test is slow, easy to skip, and burns free-tier credits if re-run carelessly. A credit-frugal, cache-aware harness makes the real-provider check repeatable and gives the reviewer concrete evidence — while keeping the irreducible human judgment down to opening one page.

## 3. Deliverable: `smoke_media` command + `make smoke-media`
`backend/apps/media/management/commands/smoke_media.py`. Runs these checks, each independently PASS/FAIL, exits non-zero if any fails. **Calls the same adapters/service the product uses — do not fork the pipeline.**

**Preflight**
- Require `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` (image) and `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` (TTS). Missing → name the exact var, exit 2, never print key values.
- Be **credit-frugal:** reuse a cached companion if one exists; generate the minimum needed to prove each path. A `--fresh` flag forces regeneration.

**Check A — real image (not placeholder).** Generate one scene through the Cloudflare adapter. Assert: HTTP 200, `Content-Type` is a real raster (`image/png` or `image/jpeg`) — **explicitly reject `image/svg+xml`** (the placeholder signature) — and bytes exceed a sane floor (e.g. > 5 KB). Report the model used.

**Check B — real audio.** Synthesize the greeting through ElevenLabs. Assert: HTTP 200, `Content-Type` is real audio (`audio/mpeg`/`audio/wav`), bytes above a floor, and the request used `ELEVENLABS_VOICE_ID`.

**Check C — cache hit.** Render the same `(style, character, scene)` twice. Assert the image provider is called on the **first** and **not** the second (0 hits → then all hits) — the `render_companion` proof, but asserted with a provider call counter.

**Check D — full reference set.** Create/refresh a companion; assert portrait + **5 expressions** + greeting audio are produced, `assets_status == ready`, exactly **7** `reference_asset_ids`, and every asset URL returns 200 with a real (non-SVG) content-type.

**Check E — safety scan ran.** Every generated `Media` row has a non-`unchecked` `safety_status`.

**Contact sheet (the human handoff).** Write `backend/media/smoke/contact-sheet.html`: the portrait + 5 expressions + the 9 `render_companion` scenes in a grid (so drift is obvious at a glance), each labelled, plus an `<audio controls>` for the greeting. Print its absolute path. This is the only thing the founder opens.

**Summary.** Print `A..E PASS/FAIL`, `OVERALL`, and the contact-sheet path. Exit 0 only if all mechanical checks pass.

## 4. Wiring
- `Makefile`: `smoke-media: ## live media smoke test (needs Cloudflare + ElevenLabs keys)` → `cd backend && python manage.py smoke_media`.
- `README.md`: a "Smoke-testing the media path" section — keys needed, `make smoke-media`, what each check means, that it costs real (free-tier) credits so it's **not** in CI, and that the founder must open the contact sheet for the visual/voice judgment.

## 5. Do NOT do today
- ❌ Do **NOT** add `smoke_media` to `make test`, pytest defaults, or CI — it makes real, paid calls. ❌ No Day-4 work. ❌ Don't log/echo key material. ❌ Don't fork the media service — assert around the real `get_or_generate_image` / adapters. ❌ Don't regenerate the full set on every run by default (credit-frugal; `--fresh` to force).

## 6. Acceptance criteria (done when ALL true)
1. `make smoke-media` with real keys runs Checks A–E and prints a PASS/FAIL summary + contact-sheet path + correct exit code.
2. Check A asserts a **real raster** image and explicitly fails on `image/svg+xml`.
3. Check B asserts real audio synthesized with `ELEVENLABS_VOICE_ID`.
4. Check C proves the cache hit with a provider call counter (1 call, then 0).
5. Check D asserts the full reference set (portrait + 5 + greeting, `ready`, 7 ids, all URLs 200 non-SVG).
6. Check E asserts every `Media` row has a real `safety_status`.
7. A contact-sheet HTML is written and its path printed; opening it shows all companion images in a grid + a working audio player.
8. `smoke_media` is absent from CI and the default suite; `make test` still makes zero live calls; `make lint && make test` green.
9. README updated; committed with a clear message.

## 7. Founder use (after this is built — the only manual step left)
`make smoke-media` → read the PASS/FAIL, then **open the printed contact-sheet path** and spend 30 seconds on the two things a script can't judge:
- **Consistency:** is it recognizably the *same character* across the 9 scenes? (Expect some drift — free `flux-1-schnell` is text-only; judge "good enough for dev," not perfect.)
- **Voice:** does the greeting sound warm/right for a young child? If not, swap `ELEVENLABS_VOICE_ID` and re-run.

## 8. End state
An opt-in `make smoke-media` harness proves the real Cloudflare + ElevenLabs path (real bytes, correct content-types, cache hit, full reference set, safety scan) with a PASS/FAIL and correct exit code, and emits a contact-sheet HTML that reduces the human check to one glance + one listen — kept entirely out of CI. The Day-3 founder test is now automated except for the irreducible visual/voice judgment.
