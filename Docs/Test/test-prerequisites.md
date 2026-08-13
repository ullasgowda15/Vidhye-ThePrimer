# What you need to test The Primer (keys, paid vs free)

> Set these in `.env` **before** running the manual test. Skipping the paid keys means you're testing offline placeholders (lexical recall, chime-not-speech, placeholder art) — which won't tell you whether the moat actually works.
>
> **Provider decision (post-audit F-3):** all four **child-data routes — beats, safety, memory, embeddings — run on paid, data-excluded tiers.** A beat prompt carries the child's name, fears, avoid-list and memories; the memory digest carries their verbatim words. The disclosure promises this data never trains anyone's model, so none of it may go to a free prompt-reselling route. This is now *enforced*: `prod.py` refuses to boot on a `:free` child-data model, and `launch_check` blocks on one. Full reasoning in `Hosting.md §3`.

## The short version

To honestly judge the two moat moments (day-two greeting + invisible learning) you need:

- **A Vertex AI service account (paid) — the three LLM jobs *and* the image gate.** Beats, the safety grader, the memory digest and the vision moderation call all run on **Gemini via Vertex AI** (`gemini-2.5-flash` for beats, `gemini-2.5-flash-lite` for safety/memory/moderation). **Not an API key and not AI Studio** — the free tier trains on prompts. One service-account JSON covers all four.
- **`OPENAI_API_KEY` (paid, pennies) — embeddings.** `text-embedding-3-small`. This is what makes Day-9 recall actually *semantic*; without it you're on the lexical `local-hash` fallback and recall quality is unverified.
- **A Google TTS key (free tier) — voice.** The greeting is *spoken* to a pre-reader, so you can't judge warmth on the offline chime. Google Cloud TTS (free 1M chars/mo) with ElevenLabs as failover.
- **Cloudflare (free tier) — images.** Workers AI for art. Moderation of that art runs on the same Vertex credentials as the LLM jobs (`IMAGE_MODERATION_PROVIDER=google`), so there is no separate vision key to obtain — but it does have to be *switched on*, see "still-owed items" below.

Everything else (Postgres, Redis) runs free/local.

## Full breakdown

| Capability | Env var(s) | Paid / Free | Needed for the test? |
|---|---|---|---|
| **Provider routing** | `AI_PROVIDER_ORDER=google` + `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION=global` + `GOOGLE_APPLICATION_CREDENTIALS` | **Paid** (service account) | **Yes** — nothing below works without it. The name is `google`, not `gemini`. |
| **Story beats** (the writing) | `AI_BEAT_MODEL=gemini-2.5-flash` (bare id, no slug, no `:free`) | **Paid** | **Yes** — quality + feel of the whole story |
| **Safety grader** (every beat vetted) | `AI_SAFETY_MODEL=gemini-2.5-flash-lite`; must differ from the beat model; `SAFETY_CLASSIFIER_ENABLED=true` | **Paid** | **Yes** — in front of every beat |
| **Memory digest** (consolidation) | `AI_MEMORY_MODEL=gemini-2.5-flash-lite`, set **explicitly** (don't let it inherit) | **Paid** | **Yes** — carries verbatim child speech; writes what tomorrow recalls |
| **Embeddings** (semantic recall — the moat) | `OPENAI_API_KEY`, `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL=text-embedding-3-small` | **Paid, pennies** | **Yes** — without it recall is lexical, not semantic |
| **Voice / TTS** | `TTS_PROVIDER=google` + `GOOGLE_TTS_API_KEY`; `TTS_FALLBACK_PROVIDERS=elevenlabs` + `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` | **Free tier** (Google 1M chars/mo) | **Yes** — the greeting is spoken |
| **Illustrations** | `IMAGE_PROVIDER=cloudflare` + `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | **Free** (~10k neurons/day) | **Yes** — for visual realism |
| **Image moderation** | `IMAGE_MODERATION=auto` + `IMAGE_MODERATION_PROVIDER=google` + `IMAGE_MODERATION_MODEL=gemini-2.5-flash-lite` | **Paid** (same service account) | **Yes** — the recall layer behind the companion-injection fix (F-1/F-2). Must actually run — see below. |
| **Database** (Postgres + pgvector) | `make up` | **Free / local** | Yes |
| **Queue** (Redis + Celery worker) | `make up` + `make worker` | **Free / local** | **Yes** — consolidation, mastery, memory run here |

## Provider routing note

The model router reaches Gemini through its **native `google` provider**, on Vertex AI with service-account auth. Three things to get right, each of which fails silently rather than loudly if you don't:

- **The provider name is `google`.** `AI_PROVIDER_ORDER=gemini` appeared in three documents and is not a registered provider; Django refuses to boot on it.
- **Model ids are bare** (`gemini-2.5-flash`), never gateway slugs (`google/gemini-2.5-flash`) — Vertex answers 404 to a slug on every call, and the boot guard now catches that shape.
- **`GOOGLE_CLOUD_LOCATION` must serve both models.** `asia-south1` serves `gemini-2.5-flash` but **not** `gemini-2.5-flash-lite`, so the grader and the digest would 404 on every call; use `global` (verified working) or `us-central1`.

And still: **never a `:free` suffix on a child-data model** — `prod.py` refuses to boot on one and `launch_check`'s `privacy.provider_terms` row blocks it. On Vertex the equivalent commitment is the *purchasing* one: a service account against a billed GCP project, not an AI Studio key.

## Two still-owed safety checks — close them here

The local manual test with real keys is the place to close the two verification gaps the audit + fix report flagged, because both need real keys you now have:

1. **Exercise the image moderator.** Keep `IMAGE_MODERATION=auto` (never `off`) **and set `IMAGE_MODERATION_PROVIDER=google`**, then confirm from the logs that it actually runs on generated images — the line to look for is `media.scan ... moderation=passed`. If you see `moderation=absent`, the gate did not run and the illustration was only structurally checked. It's the fail-closed recall layer behind the F-1/F-2 companion-injection fix; a single 1×1 PNG through `media.safety.scan` has now proven the Gemini path end to end, but it has not yet run against real generated art.
2. **Run the `--live` eval pass:** `pytest evals/ -q --live --spotcheck` (or `make evals-live`). Everything about *model behaviour* so far rests on the scripted provider; this is the missing half, and it spends a little real money.

## Cost expectation

At test scale (one founder, a 45–60 min play), spend is small and bounded: paid Gemini Flash for beats + Flash-Lite for safety/memory is a few cents to ~a dollar; embeddings are pennies; TTS and images sit inside free tiers. Beats are now a small **paid** line rather than free — that's the deliberate privacy trade. Check current Gemini/OpenAI pricing for an exact figure, since rates drift.

## Do NOT use the offline/free mode for this test

There is a plumbing-only mode (`local-hash` embeddings, `TTS_PROVIDER=local`, `IMAGE_PROVIDER=local`, a free beat slug) that proves the pipeline moves without spend — but recall is lexical, "voice" is a two-note chime, and it tells you nothing about whether the greeting lands. It is not for this test. Use the real, all-paid config above.
