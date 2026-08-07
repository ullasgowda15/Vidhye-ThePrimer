# Build — Day 3 FIX: Illustration consistency honesty, real negatives, storage isolation

> **Session type:** BUILD (fix pass for Day 3). Read this whole file first, fix everything in scope, run the **Self-review & evaluation** in §7, then do the **Commit & push** in §9. Do **not** start Day 4.
>
> **Inputs:** this file · `docs/Build/Day3.md` (original brief + acceptance criteria) · `docs/Review/reports/Day3-report.md` (the audit that produced this punch-list — file/line hints below come from it). **Source of truth:** `docs/PRODUCT.md` §8.2 (character consistency), §15.9 (negative list is partly a *safety* control), §13.8/§17.5 (per-child deletion), §16.6 (illustration service).
>
> **Why this pass exists:** Day 3 reviewed **FIX-THEN-SHIP, no P0**, but three P1s are real: the reference-image consistency mechanism never reaches the model (it's inert and misleading), the "negative prompt" is concatenated into the *positive* prompt and demonstrably injected the word "Pip" into a render, and cross-child cache reuse can place one family's asset under another family's storage prefix — a latent violation of the §13.8 deletion promise. We also fold in two cheap P2s that will bite the moment **Day 4 puts illustration on the story loop**.

---

## 1. Scope of this pass
1. **P1-1** — stop pretending to do image-reference conditioning; make consistency honest and capability-gated.
2. **P1-2** — handle negative terms correctly (real negative-prompt param where it exists; otherwise do **not** concatenate negations into the positive prompt), and add a regression guard.
3. **P1-3** — make shared/cached assets storage-isolated so cross-child reuse can never cross another child's prefix, and prove per-child deletion is safe.
4. **P2-a (fold in)** — close the concurrent-generation double-pay with a DB uniqueness guarantee.
5. **P2-b (fold in)** — put `IMAGE_MODEL` and the resolved vendor voice into the cache keys.

**Explicitly OUT of scope (do not attempt):** making free `flux-1-schnell` actually consume a reference image — it is text-only and cannot. Real image-conditioning is a later provider upgrade; this pass makes the code *honest about that*, not magical.

## 2. P1-1 — Consistency: honest and capability-gated
Current state (from the report): `build_illustration_prompt` emits a `REFERENCES:` block containing a **local file path** (`apps/media/prompts.py:90-95`) and hands it to a text-only model; `CloudflareImageProvider.generate` sends only `{"prompt","steps"}` (`adapters/image.py:88`); `character_signature` excludes `reference_url` (`prompts.py:64-79`) so removing the block wouldn't even change the cache key; `seed` is accepted then dropped (`adapters/image.py:87`).

**Do:**
- Add a capability flag to the image-provider Protocol, e.g. `supports_image_reference: bool` (Cloudflare flux-1-schnell → `False`; typed stubs that would support it → `True`).
- Emit the `REFERENCES:` block **only** when the selected provider `supports_image_reference`, and when emitted send a **provider-reachable URL**, never a storage-relative path.
- When the provider does not support it (today's default), **do not** put a file path in the prompt at all. Lean on the descriptor honestly, and leave a one-line comment in `prompts.py` stating that consistency is descriptor-only until an image-conditioning provider is wired — so the block never *reads* as a working mechanism again.
- Keep `seed` only if a provider consumes it; otherwise stop accepting it silently — comment why.

## 3. P1-2 — Negative terms: real, or removed (never concatenated)
Current state: `NEGATIVE_TERMS` (`style.py:26`) is joined into an `AVOID:` section **inside the positive prompt**; the Cloudflare payload has no `negative_prompt`. Diffusion models read `no text` / `no watermark` as the tokens *text* / *watermark* — receipt: "Pip" painted into scene 3, a watermark glyph in scene 2. §15.9's list includes **safety** items (`no realistic human children`, `no scary imagery`), so this is not merely aesthetic.

**Do:**
- Add `supports_negative_prompt: bool` to the provider Protocol.
- Where supported, pass negatives through the provider's real `negative_prompt` parameter.
- Where **not** supported (flux-1-schnell), **remove** the negations from the positive prompt entirely — concatenating them is worse than omitting them (it injects the very tokens we want gone).
- **Regression guard (mandatory, dependency-light):** in `smoke_media` Check A, assert that for a no-negative-param provider the outgoing **positive prompt contains none of the `NEGATIVE_TERMS`** (structural — catches the backfire deterministically). *Optional/best-effort:* if an OCR lib is already available, flag a render whose detected text contains the companion name; do not add a heavy new dependency just for this.

## 4. P1-3 — Storage isolation for shared/cached assets
Current state: keys embed the child id so per-child deletion is a prefix delete (`storage.py:12-14`), but cross-child reuse copies `shared.url` verbatim onto the new child's row (`service.py:170-181`), so child B's portrait can live under `assets/<child-A-id>/…`. Nothing deletes objects yet (`grep storage.delete` → none), so it's latent — fix it while cheap.

**Do (prefer the content-addressed option):**
- Store **shared/deduped** assets in a **content-addressed namespace with no child id in the path** (the `(style,character,scene)` digest already makes the key unique). Keep ownership entirely on the `Media` row via `child_id`. A child's *own* originals may keep a per-child path; only the reused/shared object must be child-agnostic.
- Add the **per-child object-deletion path** (§17.5) — delete the child's rows and any objects owned solely by that child — and a test: **deleting child A leaves child B's URLs fetchable**.
- This resolves the P2 "orphaned objects on disk" gap too (row deletion now has a matching object cleanup).

## 5. P2 folds
- **P2-a Concurrency:** add a `UniqueConstraint(type, style_hash, character_hash, scene_hash)` and use `get_or_create`-style semantics so two workers illustrating the same triple can't double-pay or write two rows. Add the migration. (The report flags this as the thing that starts happening on Day 4.)
- **P2-b Cache keys:** fold `IMAGE_MODEL` into the hashed image key (currently `style_tokens()` carries `STYLE_VERSION` but not the model — `style.py:42`, `hashing.py:34`) and fold the **resolved vendor voice** into the audio key (currently the *semantic* voice — `service.py:108`). So changing `IMAGE_MODEL` / `ELEVENLABS_VOICE_ID` / `ELEVENLABS_VOICE_MAP` invalidates stale art/audio instead of serving the old provider's output forever.

## 6. Do NOT do
- ❌ Don't start Day 4. ❌ Don't try to make flux-1-schnell consume a reference image. ❌ Don't add a heavy OCR dependency. ❌ Don't commit `.env` or `backend/media/` (both gitignored — keep it that way). ❌ Don't weaken the scan-before-store ordering or the child-never-sees-a-spinner behaviour while refactoring.

## 7. Self-review & evaluation (REQUIRED before you stop)
Do not declare done from unit tests alone. Re-verify against reality, then **write a short self-evaluation** in your final message rating each item **Verified / Partial / Unverified** with the evidence:
1. `make lint && make test` green; new migration present and `makemigrations --check` clean.
2. **Run `make smoke-media SMOKE_ARGS=--fresh`** (real providers). Confirm A–E PASS **and** the new negative-terms assertion passes. The storage-key change means old cached paths are gone, so `--fresh` is expected.
3. `render_companion <child> --scenes 3` twice → `generated: 0` on the second run (cache still correct after the key change).
4. Open the contact sheet and **inspect a fresh render for painted text** — confirm the "Pip"-in-the-corner class of failure is gone or clearly reduced. Report honestly: on a free text-only model some drift remains and is expected; what must be gone is the *injected negative tokens*.
5. New tests exist and pass: per-child deletion leaves another child's URLs fetchable; the unique-constraint prevents a duplicate row for an identical triple.
6. State plainly what you could **not** verify (e.g. real image-conditioning — out of scope by design; true visual consistency — the founder's eye on the contact sheet).

## 8. Acceptance criteria (done when ALL true)
1. `REFERENCES:`/reference-URL is emitted **only** for providers with `supports_image_reference`; no storage-relative path is ever put in a prompt; a comment makes the descriptor-only reality explicit.
2. Negatives go through a real `negative_prompt` where supported; for flux-1-schnell they are **absent from the positive prompt**; `smoke_media` Check A asserts the positive prompt contains no `NEGATIVE_TERMS`.
3. Shared/reused assets are child-agnostic in storage; a test proves deleting child A does not break child B's assets; object cleanup accompanies row deletion.
4. `UniqueConstraint(type, style_hash, character_hash, scene_hash)` + migration; identical concurrent triples cannot double-write.
5. `IMAGE_MODEL` is in the image cache key and the resolved vendor voice is in the audio cache key.
6. `make lint && make test` green; `make smoke-media --fresh` A–E + negative-terms all PASS; `render_companion` shows a cache hit on re-run.
7. Self-review & evaluation (§7) written honestly in the final message.

## 9. Commit & push (do this last)
1. `git status` — confirm **no** `.env`, no `backend/media/…`, no key material is staged (gitignore should already exclude them; verify).
2. Stage the fix code, the new migration, and the currently-untracked Day-3 docs so the remote is current through Day 3 — including `docs/Build/Day3-smoke.md`, `docs/Build/Day3-fix.md`, and the `docs/Build/Day4.md` CORS edit. `git add -A` is safe given the gitignore; still eyeball the `git status` output first.
3. Commit with a clear message, e.g. `Day 3 fix: honest consistency, real negatives, storage isolation, cache-key + concurrency guards`.
4. `git push`. Confirm the push succeeded and tell the founder to check the Actions tab is green.

## 10. End state
Consistency is descriptor-driven and *honestly* so (no inert reference block, no file path in prompts); negative/safety terms are handled through a real parameter or omitted rather than injected; shared assets are storage-isolated with a proven per-child deletion path; identical triples can't double-pay; and cache keys track the model and voice. Everything through Day 3 is committed and pushed. Real image-conditioning remains a documented later upgrade. Ready for an independent re-review (or straight to Day 4 if the founder is satisfied).
