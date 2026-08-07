# Build — Day 3: Companion Creation & Identity + Media Pipeline

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review against the acceptance criteria, then stop — do **not** start Day 4.
>
> **Source of truth:** `docs/PRODUCT.md` — §9 (Companion), §8.2 (character consistency), §15.9–15.10 (art direction / character style), §16.6 (illustration service), §5.8 (illustration pipeline), §13.8 (image safety).
>
> **If handed `docs/Review/reports/Day3-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Parent auth (JWT), COPPA‑lite consent gating child creation, child‑profile CRUD with seeds + owner permissions + throttling, parent gate + protected routes, and a tested prompt‑assembly module. **No** companion creation, story loop, or live media.

## 2. Today's goal (one sentence)
Turn the companion into a **real, persistent, visually + vocally consistent identity** — an archetype/name/color creation flow, a generated canonical descriptor + reference portrait + canonical expression set, a companion voice — and build the **cache‑first media pipeline** (image adapter → safety scan → object storage/CDN → hash cache; plus a TTS adapter) that every future illustration depends on.

## 3. Why this scope
The companion is the relationship anchor (§9). Visual + voice consistency is what makes it feel like *a friend, not a picture* (§8.2) — the single biggest quality risk of the product (§23.1). The media pipeline built here is reused by every story illustration from Day 4 on, so it must be correct **and** cache‑correct (a major unit‑economics lever, §22.4).

## 4. Tasks by workstream

### 4.1 Backend — `companion` app
```
POST /api/v1/children/{id}/companion  {archetype, name, palette, personality_preset} -> Companion
GET  /api/v1/children/{id}/companion                                                 -> Companion
```
- Enforce **1:1** with `Child` (OneToOne). Personality presets map to a stored `personality` JSON (traits/speech/values, §9.2).
- Store `visual_descriptor` (Text), `reference_asset_ids` (UUID array → `media.id`), `voice_id`, `evolution_state={"stage":0}` (§17.2).

### 4.2 Backend — `media` app (the pipeline)
`Media` model (§17.2): `child, type(image|audio), url, provider, prompt_hash, style_hash, character_hash, scene_hash, safety_status, bytes, created_at`. Index the `(style_hash, character_hash, scene_hash)` tuple.

Service — **cache‑first**:
```python
# apps/media/service.py
def get_or_generate_image(*, child, style_hash, character_hash, scene_hash, prompt) -> Media:
    hit = Media.objects.filter(style_hash=style_hash, character_hash=character_hash,
                               scene_hash=scene_hash).first()
    if hit: return hit                      # <-- provider NOT called on cache hit
    raw = image_adapter.generate(prompt)    # bytes
    scan = image_safety.scan(raw)           # -> status
    url = storage.put(child, raw)           # django-storages -> S3/R2, served via CDN
    return Media.objects.create(child=child, type="image", url=url, provider=image_adapter.name,
                                style_hash=style_hash, character_hash=character_hash,
                                scene_hash=scene_hash, safety_status=scan.status, bytes=len(raw))
```
- Wire `django-storages` to S3‑compatible storage; serve via CDN URL (never stream blobs from the app; no blobs in Postgres).

### 4.3 AI / Media adapters
- `apps/media/adapters/image.py`: provider‑swappable interface `generate(prompt) -> bytes`. Implement **one** real provider (Flux/SDXL/Imagen); leave OpenAI/Google image + others as **typed stubs** raising `NotImplementedError`.
- **Global style tokens** (§15.9): a single fixed art‑direction string + negative prompts (`no photorealism, no text, no realistic human children, no scary imagery`) stored in one place and appended to every image prompt.
- `apps/media/prompts.py::build_illustration_prompt(scene, character_refs) -> str` (Day 4/6 reuse this): scene description + style tokens + each present character's `visual_descriptor` + reference handle.
- **Image safety scan** (§13.8): `apps/media/safety.py::scan(bytes) -> Scan(status)` — a provider moderation call or a clearly‑labeled classifier stub (`# TODO: harden Day 7`). The **seam must exist** so Day 7 hardens, not adds.
- **Canonical descriptor generation:** a service that asks Claude to turn `(archetype, name, palette)` into a stable `visual_descriptor` string.
- **Reference set:** generate the companion **portrait** + a **canonical expression set** (happy, curious, proud, gentle‑concern, sleepy) once, store as `Media`, save ids to `companion.reference_asset_ids` (§9.6).
- `apps/media/adapters/tts.py`: provider‑swappable `synthesize(text, voice_id) -> bytes`; one real provider; synthesize the companion's spoken "hello".

### 4.4 Frontend — companion picker + greeting scene
- `src/routes/parent/CompanionPicker.tsx` (part of onboarding): choose archetype (few, illustrated options, §14.5), name (child‑nameable text input), color/palette swatches.
- `src/routes/child/MeetCompanion.tsx` (§4.3): render the portrait + auto‑play the TTS hello + a by‑name welcome; alive with motion (§14.7). This is the imprint moment — make it feel warm, not like a form result.

## 5. Key stubs
```python
def hashes_for(style_tokens, character_descriptor, scene_desc):
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:16]
    return h(style_tokens), h(character_descriptor), h(scene_desc)
```
- Keep the safety scan a labeled stub if needed, but do NOT skip the call site.
- Expose a dev command `python manage.py render_companion <child_id> --scenes 3` to eyeball consistency.

## 6. Do NOT do today
- ❌ No story generation/beats (Day 4). ❌ No memory. ❌ No companion **evolution over time** logic (stage 0 only; growth is §9.4, later). ❌ Do NOT skip caching "to move faster" — caching is the point. ❌ No realistic/photoreal art (negative prompts enforce this).

## 7. Acceptance criteria (done when ALL true)
1. A companion is created 1:1 for a child (archetype/name/palette/personality) and persists; GET returns it.
2. A canonical `visual_descriptor` is generated and stored.
3. Portrait + the **5 canonical expressions** are generated, stored in object storage, and served via CDN URLs; `companion.reference_asset_ids` references them.
4. **Cache proof:** generating the same `(style,character,scene)` twice calls the image provider **once** (assert via a provider call counter/log).
5. Every `Media` row has a `safety_status`; the scan runs before store.
6. TTS produces the companion "hello" in the assigned `voice_id`; it plays in `MeetCompanion`.
7. Consistency: `render_companion --scenes 3` yields 3 images that are recognizably the **same** character (attach them).
8. Companion picker + greeting scene work end‑to‑end in the UI; `make lint && make test` + **CI green**.

## 8. Founder manual test
In onboarding, create + name a companion, reach "meet your companion" → see a consistent portrait, hear the hello. Re‑enter the same scene → confirm it's cache‑served (instant, no new provider cost in logs). Run `render_companion --scenes 3` and eyeball "same character."

## 9. End state to hand to Day 4
Companions have a persistent identity (descriptor + reference portrait + 5 canonical expressions + voice). A cache‑first media pipeline (image adapter + global style tokens + `build_illustration_prompt` + safety‑scan seam + object storage/CDN + hash cache, and a TTS adapter) is live and reusable. A "meet your companion" greeting scene works. Still **no** story loop, memory, or runtime safety hardening.
