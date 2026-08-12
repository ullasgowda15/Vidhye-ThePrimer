# What you need to test The Primer (keys, paid vs free)

> Set these in `.env` **before** running the manual test. Skipping the paid/embedding keys means you're testing offline placeholders (lexical recall, chime-not-speech, placeholder art) — which won't tell you whether the moat actually works.

## The short version

To honestly judge the two moat moments (day-two greeting + invisible learning) you need, at minimum:

- **`ANTHROPIC_API_KEY` — PAID.** Powers the beat model, the safety grader, and the memory digest. This is the biggest lever on *feel* and *speed*.
- **`OPENAI_API_KEY` — PAID (but pennies).** Powers embeddings. **This is the one that makes Day-9 recall actually semantic** — without it you're on the `local-hash` lexical fallback and recall quality is unverified.
- **One TTS key — FREE tier available.** The greeting is *spoken* to a pre-reader, so you can't judge its warmth on the offline chime. ElevenLabs (free credits) **or** Google TTS (free 1M chars/month).

Everything else (Postgres, Redis, images) can run free or offline.

## Full breakdown

| Capability | Env var(s) | Paid / Free | Needed for the test? |
|---|---|---|---|
| **Story beats** (the writing) | `ANTHROPIC_API_KEY` (default `AI_PROVIDER_ORDER=anthropic`, `AI_BEAT_MODEL=claude-opus-5`) | **Paid** | **Yes** — quality + speed of the whole story |
| **Safety grader** (every beat vetted before a child sees it) | `AI_SAFETY_MODEL` (default `claude-haiku-4-5`, same Anthropic key), `SAFETY_CLASSIFIER_ENABLED=true` | **Paid** (same key) | **Yes** — it's in front of every beat |
| **Memory digest** (consolidation) | `AI_MEMORY_MODEL` (inherits the safety model / Anthropic key) | **Paid** (same key) | **Yes** — writes what tomorrow recalls |
| **Embeddings** (semantic recall — the moat) | `OPENAI_API_KEY`, `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL=text-embedding-3-small` | **Paid, negligible cost** | **Yes** — without it recall is lexical, not semantic |
| **Voice / TTS** (spoken greeting + narration) | `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` | **Free tier** (limited credits) | **Yes** (one TTS option) |
| ↳ TTS fallback / free alternative | `GOOGLE_TTS_API_KEY` (`TTS_FALLBACK_PROVIDERS=google`) | **Free** (1M chars/mo, Neural2) | Either this or ElevenLabs |
| **Illustrations** | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` (`IMAGE_PROVIDER=cloudflare`, flux-1-schnell) | **Free** (~10k neurons/day) | **Recommended** — for visual realism; can skip with `IMAGE_PROVIDER=local` (placeholder art) |
| **Image moderation** | `IMAGE_MODERATION=auto`, Anthropic vision (`claude-haiku-4-5`) | **Paid** (same Anthropic key) | If images on. For a quick local run only, `IMAGE_MODERATION=off` is structural-only (prod refuses `off`) |
| **Database** (Postgres + pgvector) | `make up` (docker) | **Free / local** | Yes |
| **Queue** (Redis + Celery worker) | `make up` + `make worker` | **Free / local** | **Yes** — consolidation + mastery run here |

## Privacy rule (don't skip)

Do **not** use prompt-reselling "free" routes for the **safety**, **memory**, or **embedding** models (§13.8) — those paths see children's data. The OpenAI *API* and Anthropic both exclude API inputs from training, which is why they're the right homes for these. Free OpenRouter routes are only acceptable for the **beat** model, not the three above.

## Cost expectation

The recurring cost during a test session is the **Anthropic model calls** (one beat + one safety grade per beat, plus a memory digest per session ended). Embeddings and free-tier TTS/images are effectively negligible. A 45–60 min play is a small, bounded spend — but check **current** Anthropic/OpenAI pricing for an exact figure, since rates change.

## Two ways to run

**A) Full-fidelity (recommended for judging the moat)** — Anthropic + OpenAI embeddings + a real TTS key + Cloudflare images. This is the only configuration that tells you the truth about the product.

**B) Free/offline (plumbing only, NOT for judging feel)** — `AI_PROVIDER_ORDER=openrouter` with a free beat slug, `local-hash` embeddings, `TTS_PROVIDER=local`, `IMAGE_PROVIDER=local`. Everything runs, nothing costs, but recall is lexical, "voice" is a two-note chime, and beats can take 35–90s. Use only to confirm the pipeline moves — never to decide whether the greeting lands.

> If you switch `AI_PROVIDER_ORDER`, remember `AI_BEAT_MODEL` **and** `AI_SAFETY_MODEL` must both be in that provider's form (a Claude id for anthropic, a `vendor/model` slug for openrouter) — Django refuses to boot on a mismatch.
