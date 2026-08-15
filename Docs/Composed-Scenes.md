# Composed Scenes — illustration without per-beat generation

> **Status:** proposal, not built. Written 2026-08-15 after the pilot spent an
> evening serving beats with no pictures.
>
> **Source of truth:** `Docs/PRODUCT.md` — §5.8 (illustration), §8.2 (character
> consistency), §15.9 (visual safety), §16.6 (media pipeline), §4.9 (never a
> spinner, never an error).

---

## 1. Why

On 2026-08-15, three consecutive beats in one adventure. Nine image requests in
two minutes:

```
05:05:13  200 200 200      beat 1 — complete
05:06:24  429              beat 2 — illustration lost
05:06:26  200 200          …its choice icons survived
05:07:11  429
05:07:12  429 429          beat 3 — no pictures at all
```

Two limits, hit in sequence. Cloudflare returned `you have used up your daily
free allocation of 10,000 neurons` — a hard daily cap, correctly benched for six
hours. That pushed every illustration onto the Gemini fallback, which then
returned `RESOURCE_EXHAUSTED` — a *per-minute* rate limit, not a spent budget.
The tell is 05:06:24: one call 429s and two seconds later two more succeed.

What tipped it over is the shape of our own pipeline. A beat is not one image
request, it is **three fired simultaneously** — the illustration and both choice
icons (`story.media.parallel ... overlap_ms=16115`). Beats arrive roughly every
fifty seconds, so we spike three concurrent calls repeatedly against a default
quota that has never been raised.

The safety layer held: `blocking=-` on the unfinished beat means the voice was
present, so the child could hear the story and tap the choices. A missing picture
is a playable beat. But a pre-reader looking at a blank frame is not the product.

Two more costs that were already true before that evening:

* **Character consistency (§8.2).** The companion's reference set was drawn by
  FLUX. Gemini has never seen it, so every fallback beat shows a companion that
  is subtly the wrong animal. `Media.provider` records which model drew each row,
  which makes those beats findable and does not make them right.
* **Money.** A dozen images per adventure, per child, per session, forever.

Raising quotas and paying Cloudflare $5/month fixes the outage. Neither fixes the
other two, and neither changes the fact that we are paying inference prices for
novelty a four-year-old did not ask for.

## 2. The reframe

The expensive thing is not the picture. It is generating a **novel** picture per
beat.

A child does not need novelty per beat. They need **correspondence**: what they
see must change when what they hear changes, and the character must be the same
character. Correspondence is cheap. Novelty is what we are paying for, and it is
the part that keeps failing.

So: compose scenes from a pre-generated library instead of generating them.

## 3. What stays generated

**One establishing image per adventure**, where novelty genuinely earns its cost —
the first frame, the one that says *this story is about your thing*. Everything
after it composes.

That alone is roughly a 12× reduction, and it fits inside the current quota with
no plan upgrade.

Generation also stays as the **fallback** for a scene the library genuinely cannot
express. The polarity inverts: composition is the primary path, generation is the
exception. Today it is the reverse, which is why an outage is a blank screen.

## 4. The asset library

Generated once, stored in R2, never regenerated. One art direction chosen for
composability — flat vector or silhouette reads clearly at low detail and cuts
the asset count by an order of magnitude, because a shape carries a scene where a
rendering has to be correct.

| Layer | Source | Rough count |
|---|---|---|
| Backgrounds | one per place, per mood/time-of-day | 30–40 |
| Companion sprites | `CANONICAL_EXPRESSIONS` × 3 poses, transparent PNG | ~15 per companion archetype |
| Props | objects the story engine can name — a blue ball, a sparkling collar, paw prints | ~50, growing |
| Choice icons | keyed by **verb** (look, follow, help, hide), not per-choice | ~12 |

The places already exist as data: `apps/story/worlds.py:select_world` picks a
world and a place (`whispering_woods` → `the Hollow Oak`), and
`apps/memory/models.py:WorldPlace` persists which ones a child has discovered. The
expressions already exist as `apps.companion.models.CANONICAL_EXPRESSIONS`
(`happy`, `curious`, `proud`, `gentle_concern`, `sleepy`). This is not a new
taxonomy, it is art for one we already have.

Choice icons keyed by verb rather than by choice text is the single biggest saving:
two thirds of today's image spend is choice icons, and there are perhaps a dozen
verbs across every story we will ever tell.

## 5. Schema

```
SceneLayer   kind (background|character|prop|icon), key, media_id, tags[]
Scene        ordered [SceneLayer + x, y, scale, z]
Beat         scene_id  (replaces the single media_id for illustration)
```

`Beat.audio` is untouched. Voice does not change here at all.

## 6. The beat contract picks layers, not prompts

The largest change, and the one that makes quality go **up**:

```
scene: { background: <key>, expression: <key>, props: [<key>, ...] }
```

The vocabulary is passed in the prompt as an enumeration. Constrained choice from
a list is a far easier task for a model than writing a good image prompt, and it
is checkable — an unknown key is dropped, never rendered. Today a bad prompt
produces a bad picture that ships; here a bad key produces a slightly emptier
scene and a metric.

This is the same move `apps/story/worlds.py` already makes for places, extended
one level down.

## 7. Runtime — compose on the client

Send the layer list as JSON; the PWA stacks positioned `<img>` elements.

* No image processing on the server, so no CPU cost and no new dependency.
* No stored artefact per beat — nothing to write to R2, nothing to moderate,
  nothing to cache-bust.
* Layers cache in the browser and are reused across every beat of the session,
  so the second beat of an adventure paints instantly.

`apps/media/storage.public_url` already returns unsigned, edge-cacheable,
content-addressed URLs (`querystring_auth: False`, `max-age=31536000, immutable`).
Layers are exactly the asset that design was written for.

## 8. Motion is what makes it not a slideshow

Composed stills feel dead; composed stills that move feel alive, and this is free:

* slow pan and zoom on the background,
* parallax between background and foreground props,
* a sprite that breathes or blinks,
* a prop that drifts in as the narration reaches it.

Timed against the narration's word boundaries, this is a stronger correspondence
than a generated still has ever given us — the picture moves when the voice moves.

## 9. Safety

Composition **strengthens** §15.9. Every layer is pre-vetted once, by a human,
before it enters the library. There is no per-beat image reaching a child that no
one has ever looked at — which is what `IMAGE_MODERATION=auto` exists to
approximate today and what it degrades to `moderation=absent` without.

The moderation gate stays for the establishing shot and the generation fallback.

## 10. Rollout

Behind `media.composition`, alongside generation, with the existing
`media.illustrations` lever untouched:

1. **Slice 1 — library + schema.** `SceneLayer`, `Scene`, the R2 upload path, an
   admin view to eyeball the set. No runtime change.
2. **Slice 2 — the contract.** `scene` on the beat, vocabulary in the prompt,
   validation that drops unknown keys. Composed scenes served behind the flag.
3. **Slice 3 — client composition + motion.** The PWA stacks and animates layers.
4. **Slice 4 — invert the polarity.** Composition primary, generation for the
   establishing shot and as fallback. Retire the per-beat illustration call.

Each slice is deployable and reversible on its own.

## 11. Metrics that decide whether this worked

* `scene.prop_missing key=<key>` — the story asked for something the library does
  not have. **This is the roadmap**: it tells you exactly what to draw next.
* Images generated per adventure. Target: 1.
* Beats served with no visual at all. Target: 0, including during an outage.
* Cost per session (`/admin/metrics`, §22.4).

## 12. The real risk

**A finite visual vocabulary.** A story about a purple submarine, with no
submarine layer, shows something approximate.

Generation's whole promise was that it never has to be approximate. That promise
is currently being paid for in rate limits, a companion that changes species
mid-adventure, and blank frames — which is a worse approximation than a
well-chosen library.

Mitigations: let the story engine see the vocabulary and steer toward what exists
(it already does this for worlds and places); keep generation as the fallback for
the genuinely novel; and let `scene.prop_missing` drive what gets drawn next.

## 13. The thing worth building even if none of this is

**Let the child draw it.** "What do you think the Hollow Oak looks like?" — and
the app keeps their drawing as that place's illustration for every future visit.

It costs nothing, it turns a missing asset into agency, it is a memory the parent
dashboard can show, and no competitor with a bigger inference budget can copy it.
That is a stronger product than generation, not a fallback from it.
