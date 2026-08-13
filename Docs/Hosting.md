# Hosting & Infrastructure — The Primer

> **Status:** Decided for the pilot (15 users) and the following ~3 months. Owner: Appu.
> **Decision in one line:** Run the whole stack on a **Google Cloud VM funded by the $300 free credit**, deployed as a **portable `docker-compose`** (nothing GCP-proprietary), with a planned move to **Hetzner (~$5/mo)** around day 90 when the credit ends.
> **Why this shape:** free now (credit + free tiers), one dashboard, and — because we self-host every stateful piece — migrating later is copying a compose file, not re-architecting.

---

## 1. The one rule that makes migration painless

**Run our own Postgres, Redis, Django and Celery in containers on a plain VM. Never use a provider's *managed* database/cache/runtime (Cloud SQL, Memorystore, Cloud Run).**

That single discipline is the difference between "migration is an afternoon" and "migration is a rewrite." Everything below follows from it. Our stack is already a `docker-compose` in `infra/`, so this is the natural path, not extra work.

What keeps us portable, piece by piece:

- **Compute** — `docker-compose` on a VM → lifts to any VPS unchanged.
- **Database & cache** — Postgres (with pgvector) and Redis run as *our* containers → they move with the compose file, data and all.
- **LLM** — our model router is provider-agnostic by design (Gemini today, swap by config later). No lock-in.
- **Media storage** — Cloudflare R2 (S3-compatible), **not** Google Cloud Storage → media moves too.
- **Voice** — Google TTS, but the adapter already fails over to ElevenLabs → not tied to one vendor.

---

## 2. What we're outsourcing — from whom, what, at what cost

| Component | Vendor | What it does | Pilot tier | Pilot cost | Cost after free tier |
|---|---|---|---|---|---|
| **Hosting / compute** | Google Cloud (Compute Engine) | One VM runs the entire `docker-compose` (Django ASGI, Celery worker, Postgres+pgvector, Redis) | e2-medium (4 GB), on the **$300 / 90-day credit** | **$0** | ~$24–27/mo on GCP *(reason to migrate to Hetzner ~$5)* |
| **Story beats (LLM)** | Google — **Vertex AI** Gemini (paid, data-excluded) | Writes the story beats | `gemini-2.5-flash`, service-account auth — *not* the AI Studio free tier (§3) | ~$2–5/mo *(from credit)* | $0.30/$2.50 per 1M tokens |
| **Safety grader + memory digest (LLM)** | Google — **Vertex AI** Gemini (paid, data-excluded) | Independent output classifier (§13.3) + memory consolidation | `gemini-2.5-flash-lite` | ~$1–3/mo *(from credit)* | $0.10/$0.40 per 1M tokens |
| **Image moderation (vision)** | Google — **Vertex AI** Gemini (paid, data-excluded) | The §13.8 gate: looks at every generated illustration before a child does | `gemini-2.5-flash-lite`, same credentials | pennies *(from credit)* | as above, plus image tokens |
| **Embeddings** | OpenAI | Semantic memory vectors for recall (the moat) | `text-embedding-3-small` | **<$1/mo** | ~$0.02 per 1M tokens |
| **Images** | Cloudflare — Workers AI | Companion reference set + scene art (FLUX-1-schnell) | 10,000 neurons/day **free** | **$0** | $0.011 per 1,000 neurons |
| **Voice** | Google Cloud — Text-to-Speech | Narration + returning-greeting audio | Neural2, **1M chars/mo free** | **$0** | ~$16 per 1M chars (Neural2) |
| **Media storage + CDN** | Cloudflare — R2 | Stores & serves generated audio + images | 10 GB free, **free egress** | **$0** | $0.015/GB-mo storage |
| **Voice failover** | ElevenLabs | Backup TTS if Google TTS is down/over quota | Free tier | **$0** | pay-as-you-go if promoted to primary |
| **Domain** (optional) | any registrar | pilot URL | — | ~$1/mo | same |

**Total out of pocket for the pilot: ≈ $0**, covered by the GCP $300 credit and recurring free tiers. Real recurring spend (~$5/mo) begins only when we move compute off the credit.

> ⚠️ **Prices drift — verify before relying on a number.** The figures above are approximate as of the plan date. Check each vendor's current pricing page before committing, especially the Gemini and TTS per-million rates.

---

## 3. The critical privacy rule (§13.8) — do not break this

**Children's data must never train someone else's model.**

**All four LLM/embedding routes run paid + data-excluded. Including beats.** That is a change from the first version of this document, made after the Day 1–15 audit (F-3), and the reason is that the earlier split was wrong on the facts:

- A **beat prompt is not "just story text".** It carries the child's first name, their age band, the full avoid-topics list, the full fears list and the retrieved memory bundle. There is no version of "the free tier may train on this" that a parent would recognise as acceptable.
- The **memory digest** carries the *verbatim* session digest — `The child said "…"` for every turn of the sitting. That is the least-processed child speech in the system.
- The **safety grader** is handed the beat and the child's controls; the **embeddings** are handed each memory's sentence.
- `apps/accounts/privacy.py` tells parents, in the present tense and with no qualification: *"We never use your child's data to train anybody's AI models — not ours, and not our providers."* A carve-out for beats would make that sentence false, and the disclosure is the thing this rule exists to keep true.

So: never a free prompt-reselling route for **any** of them. On OpenRouter that means never a `:free` suffix; on Gemini it means **Vertex AI, authenticated by a service account** — billed to the GCP project and covered by the enterprise terms that exclude inputs from training — rather than an AI Studio key, whose free tier trains on prompts. That is enforced in the shape the code can enforce it: `apps/common/vertex.py` builds the client with `vertexai=True` and a service-account credential and there is no API-key setting for Gemini anywhere in `settings/base.py`, so the route that would break the promise is not one this deployment can be configured to take. Also set the account-level data policy (OpenRouter: disallow prompt logging/training) — the suffix is what code can check, the account setting is the other half.

**This is now enforced, not just written down** (that is the other half of F-3 — the rule had been in `PROGRESS.md` since §13.8 was written and every model was on a `:free` route anyway):

- `apps/common/model_ids.py` owns the rule (`is_free_route` / `permits_training` / `training_error`).
- `config/settings/prod.py` **refuses to boot** if any of `AI_BEAT_MODEL`, `AI_SAFETY_MODEL`, `AI_MEMORY_MODEL`, `EMBEDDING_MODEL` is a `:free` route.
- `manage.py launch_check` has a **blocking** `privacy.provider_terms` row. It used to report only that the grader was *reachable*, which is how a `:free` grader passed the preflight.
- `flags._validate_beat_model` rejects one too, so the no-deploy rollback lever cannot reach a state the boot guard forbids.

What none of that proves is that a given *paid* route's terms actually exclude training — that is a purchasing decision, and it belongs to whoever signs up. The guard catches the specific cheap mistake that has already happened once.

---

## 4. Configuration — the exact switches

Set in `.env` (never commit it — it's gitignored). Values are the pilot targets.

```dotenv
# --- Provider routing: ALL FOUR paid + data-excluded (§3) ---
AI_PROVIDER_ORDER=google             # the registered provider name (NOT 'gemini')
AI_BEAT_MODEL=gemini-2.5-flash               # PAID/data-excluded (bare id, no slug, no ':free')
AI_SAFETY_MODEL=gemini-2.5-flash-lite        # PAID/data-excluded — must differ from beat model
AI_MEMORY_MODEL=gemini-2.5-flash-lite        # PAID/data-excluded — carries the verbatim digest

# --- Vertex AI auth: a service account, never an API key (§3) ---
GOOGLE_CLOUD_PROJECT=<gcp-project-id>
GOOGLE_CLOUD_LOCATION=global         # see the region note below
GOOGLE_APPLICATION_CREDENTIALS=/home/primer/.config/primer/vertex-sa.json
GOOGLE_GENAI_USE_VERTEXAI=true

# --- Embeddings (semantic recall) ---
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=<key>                 # pennies; the OpenAI API excludes inputs from training

# --- Images ---
IMAGE_PROVIDER=cloudflare
CLOUDFLARE_ACCOUNT_ID=<id>
CLOUDFLARE_API_TOKEN=<token>
IMAGE_MODERATION=auto                # vision moderation on
IMAGE_MODERATION_PROVIDER=google     # runs the gate on Vertex — no Anthropic key needed
IMAGE_MODERATION_MODEL=gemini-2.5-flash-lite

# --- Voice ---
TTS_PROVIDER=google                  # Google Cloud TTS, free 1M chars/mo
GOOGLE_TTS_API_KEY=<key>
GOOGLE_TTS_VOICE=en-US-Neural2-F
TTS_FALLBACK_PROVIDERS=elevenlabs    # failover
ELEVENLABS_API_KEY=<key>
ELEVENLABS_VOICE_ID=<id>

# --- Media storage: Cloudflare R2 (S3-compatible) ---
MEDIA_STORAGE=s3
AWS_ACCESS_KEY_ID=<r2-access-key>
AWS_SECRET_ACCESS_KEY=<r2-secret>
AWS_STORAGE_BUCKET_NAME=<bucket>
AWS_S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com
AWS_S3_REGION_NAME=auto
MEDIA_CDN_BASE_URL=<r2-public-or-custom-domain>
```

> **Router note (resolved — this was §11's open decision 4).** The router has a **native `google` provider** (`apps/story/ai/router.py::GoogleProvider`), on the `google-genai` SDK in Vertex mode. Routing Gemini through OpenRouter is no longer the plan and no longer needed. Two things this changes for whoever sets it up:
> - The provider name is **`google`**. `AI_PROVIDER_ORDER=gemini` — which every earlier version of this document said — is not a registered provider and Django refuses to boot on it.
> - Model ids are **bare Vertex ids** (`gemini-2.5-flash`), never gateway slugs (`google/gemini-2.5-flash`). `model_ids.pairing_error` refuses a slug under `google`, because Vertex answers 404 to one on every call.

> **Region: `asia-south1` cannot run this product.** Verified against the live API on 13 Aug 2026, not read off a docs page. `asia-south1` (Mumbai) serves `gemini-2.5-flash` but **not** `gemini-2.5-flash-lite`, so the safety grader and the memory digest would 404 on every call — and since §13.3 requires the grader to be a *different* model from the writer, there is no single-model configuration that fixes it. `global` and `us-central1` serve both; `global` is what §7 deploys, because Google routes each request to the nearest healthy region and that beats pinning us-central1 from India. Watch out for the trap: `client.models.get()` reports Flash-Lite as present in `asia-south1` (it resolves against the global publisher catalogue) and `generate_content` then 404s — **probe with a real generation.**

> **Boot guards to expect (all correct, don't disable):** `prod.py` refuses to start with the safety classifier off, with `IMAGE_MODERATION=off`, with `MEDIA_STORAGE=local`, with any child-data model on a `:free` route (§3), or with `google` selected and any of `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` / `GOOGLE_APPLICATION_CREDENTIALS` missing or pointing at a file that is not there. These are safety rails, not bugs — the last one exists because a half-configured Vertex box does not crash: it quietly finds no provider, serves pre-vetted fallback beats to every child, and reports `moderation=absent` on every scan.

> **The service-account key never goes in the repository.** Keep it outside the tree entirely (`~/.config/primer/vertex-sa.json`, owner-read-only: `chmod 600`, or `icacls <path> /inheritance:r /grant:r "$env:USERNAME:(F)"` on Windows) and mount it into the container. `.gitignore` has patterns for the obvious filenames, but they only catch the names somebody thought of — one arrived as `gemini-key.json` at the repo root, which `*.pem` and `*.key` did not match. If a key has ever been in the tree, in a synced folder, or in a chat, rotate it in GCP rather than reasoning about who saw it.

---

## 5. Cost controls — so we never get surprised

Non-negotiable at a tight budget:

1. **GCP billing budget + alert.** Set a budget on the billing account with email alerts at **$50 / $150 / $250** of the $300, so the credit can't silently drain. (Note: GCP alerts *notify*, they don't hard-stop — see #4.)
2. **Beats are a paid line item now** (§3), so the credit covers the VM *and* the LLM calls. **Measured, not estimated:** the first real beat through Vertex cost **$0.0031** (986 input + 1,136 output tokens on `gemini-2.5-flash`, priced by `analytics.pricing`). At ~90–120 beats/day that is roughly **$0.30–0.40/day, ~$10/month** — a little more than the earlier guess, because Gemini's *thinking* tokens bill at the output rate and are reported separately (`router._gemini_usage` folds them in, so cost/session already counts them). Against a $300 credit that is comfortable. Watch it in `manage.py metrics` (cost/session) rather than assuming a free tier absorbs it.
3. **OpenAI usage hard-limit.** Set a low monthly cap (e.g. $5) in the OpenAI dashboard — embeddings won't come close, so this is pure insurance.
4. **Hard-stop billing (optional but recommended).** A small Cloud Function tied to the budget pub/sub topic that disables billing at 100% of credit prevents any post-credit charge. Ask me to add this if you want a true hard ceiling.
5. **Cloudflare & Google free tiers are self-capping** — they degrade or 429 rather than bill you, and the app handles that gracefully (see #6).

---

## 6. Free-tier limits & the two tightest constraints

At 15 users (~90 beats/day) almost everything is comfortably inside free tiers. The two to watch:

- **Voice — Google TTS 1M chars/month.** ~90 beats/day × ~400 chars ≈ ~1M/mo, i.e. *right at the ceiling*. Mitigations already in the app: TTS is cached on `(voice, text)`, so repeated greetings/phrases don't re-synthesize; ElevenLabs is the failover. If we cross 1M, overflow is ~$16/1M chars, or Standard voices give 4M/mo free.
- **Images — Cloudflare Workers AI 10k neurons/day (shared pool).** Companion creation is 6 image calls per child (one-time); per-beat art is the ongoing draw. FLUX is a larger model, so watch the daily pool. Overflow is cheap ($0.011/1k neurons). The app caches media, so identical scenes don't re-generate.

**Why running near free tiers is safe here:** the product fails *gracefully* by design — a quota 429 degrades to the pre-vetted fallback beat and cached/failover voice; a child never sees a spinner or a raw error. That's what makes a near-zero-cost pilot acceptable with real families.

---

## 7. Deployment runbook (pilot, on GCP)

1. **GCP project + billing.** Create a project; enable billing (activates the $300 credit and unlocks TTS + Vertex/Gemini). Set the budget alerts from §5.
2. **VM.** Compute Engine → e2-medium (4 GB), Ubuntu 22.04 LTS, 30 GB disk, region **nearest your users** (e.g. `asia-south1`/Mumbai for India — *confirm this*). Allow HTTP/HTTPS.
3. **Harden.** Non-root user; `ufw` allow 22/80/443 only; SSH keys only (no password); unattended-upgrades on.
4. **Docker.** Install Docker + Docker Compose plugin.
5. **Code + secrets.** Clone the repo; create `.env` from §4 on the box (never commit it).
6. **TLS + reverse proxy.** Put Caddy (auto-HTTPS) or nginx+certbot in front of uvicorn on 443. (Add the proxy to `infra/docker-compose` or run alongside.)
7. **Bring it up.** `make up` (Postgres+Redis) → `make migrate` → app container (uvicorn) + `make worker` (Celery). Confirm `make ping` (worker live) and `make hello-beat` (AI keys work).
8. **Storage.** Point media at R2 (§4); confirm an image/audio round-trips to the bucket and serves via the CDN URL.
9. **Smoke test.** Run the Day-10/11 manual test against real providers — greeting warmth, invisible learning, two different worlds.
10. **Backups.** Nightly `pg_dump` to R2 (cron). *Do this before onboarding real families — it's the one irreplaceable thing.*
11. **Uptime monitoring.** A free external ping (e.g. UptimeRobot) on the health endpoint so you know if the box goes down.

---

## 8. Day-90 migration runbook (GCP → Hetzner)

Trigger this around day 75–80, before the credit expires, so there's no scramble.

1. Provision a **Hetzner CX22** (~€4–5/mo), Ubuntu 22.04, same hardening (§7.3–7.4).
2. Install Docker; clone the repo; copy the `.env` across (rotate any keys you'd rather not reuse).
3. **Move the data:** `pg_dump` from GCP → restore into the Hetzner Postgres container. R2 media and all API keys are unchanged (that's the point of R2 + a portable router — nothing to migrate there).
4. `make migrate` (no-op if schema matches) → bring up app + worker → `make ping` / `make hello-beat`.
5. Repoint DNS to the Hetzner IP; verify HTTPS; run the smoke test.
6. Decommission the GCP VM once the new box is confirmed healthy for 24–48h.

Because Postgres/Redis were always our containers and media was always on R2, **the only thing that actually moves is the database dump.** Everything else is redeploying the same compose.

---

## 9. Scaling trajectory (the coming months)

| Phase | Users | Host | LLM | Notes |
|---|---|---|---|---|
| **Pilot** | 15 | GCP VM on $300 credit | Gemini Flash paid + Flash-Lite paid (§3) | ≈$0 out of pocket (credit) |
| **Post-credit** | 15–50 | Hetzner CX22 (~$5/mo) | same | trivial migration; costs stay tiny |
| **Growth** | 100s | Bigger Hetzner VPS | same, watch free-tier caps | per-user costs scale ~linearly; still self-hosted, no re-architecture |
| **Scale** | 1,000s | Multi-VM or managed PG + autoscaling + edge CDN | negotiate/optimize model mix | a good problem, months away; revisit managed services *then*, deliberately |

The architecture doesn't change between pilot and growth — only the size of the box. That's the payoff of the §1 rule.

---

## 10. Risks & how we've covered them

- **GCP credit cliff at day 90** → planned migration (§8) triggered early; budget alerts (§5) give warning.
- **Free-tier exhaustion mid-session** (voice/images) → graceful degradation + failover already built (§6).
- **Vendor lock-in** → self-hosted stateful services + S3-compatible storage + provider-agnostic LLM router (§1).
- **Data loss** → nightly `pg_dump` to R2 before onboarding real users (§7.10). *Highest-priority ops task.*
- **Privacy / child data** → paid data-excluded tier for safety/memory/embeddings (§3).
- **Cost surprise** → budget alerts + usage caps + optional hard-stop function (§5).
- **Latency on real providers** → Gemini Flash is fast; pick the VM region near your users; TTS/image caching reduces repeat calls.

---

## 11. Open decisions to confirm (defaults chosen, correct me if wrong)

1. **VM region** — defaulted to nearest your users (`asia-south1`/Mumbai if India-based). *Confirm your users' location.* Note this is the **compute** region and is independent of `GOOGLE_CLOUD_LOCATION`, which must be `global` or `us-central1` because `asia-south1` does not serve Flash-Lite (§4).
2. **Embeddings** — recommend OpenAI `text-embedding-3-small` (pennies) over free `local-hash`, because semantic recall is the moat. `local-hash` is $0 but lexical; fine only if you want zero extra vendors at launch.
3. **Day-90 plan** — assumed migrate to Hetzner. Alternative: stay on GCP paid (simpler, but ~5× the compute cost).
4. ~~**Native Gemini provider vs OpenRouter**~~ — **decided and built.** The native `google` provider runs on Vertex with service-account auth (§4 note). OpenRouter stays wired as a seam, unused by this deployment.
5. **Domain** — do we want a custom pilot URL, or is the VM IP / a free subdomain fine for 15 users?

---

## 12. Accounts to create / keys to obtain (checklist)

- [ ] Google Cloud account + project + billing enabled (unlocks $300 credit, TTS, Vertex AI)
- [ ] **Vertex AI API enabled** on the project
- [ ] **Service account + JSON key** with the `roles/aiplatform.user` role — this is the credential for beats, safety, memory *and* image moderation, all four. **No API key of any kind** (§3): an AI Studio key is a different product under different terms. Store the JSON outside the repo, owner-read-only (§4).
- [ ] Google Cloud Text-to-Speech API enabled + key
- [ ] OpenAI account + API key (embeddings) — with a low usage cap
- [ ] Cloudflare account → Workers AI token + Account ID (images)
- [ ] Cloudflare R2 bucket + access keys + public/CDN URL (media)
- [ ] ElevenLabs account + API key + a voice ID (voice failover)
- [ ] (Day 90) Hetzner account
- [ ] Budget alerts configured on GCP and OpenAI **before** onboarding users
