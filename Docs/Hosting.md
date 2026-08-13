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
| **Story beats (LLM)** | Google — Gemini API (AI Studio) | Writes the story beats | Gemini **Flash — free tier** (~1,500 req/day; our load ≈90–120/day) | **$0** | ~$1.50/$7.50 per 1M tokens if we outgrow free RPM |
| **Safety grader + memory digest (LLM)** | Google — Gemini API (**paid**, data-excluded) | Independent output classifier (§13.3) + memory consolidation | Gemini **Flash-Lite**, paid tier | ~$1–3/mo *(from credit)* | ~$0.10/$0.40 per 1M tokens |
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

- The **free** Gemini tier (AI Studio) **trains on your prompts.** That is acceptable **only** for the **beat** model (story text).
- The **safety grader, memory digest, and embeddings** see the child's actual history and must run on a **paid, data-excluded** tier (Gemini paid / Vertex, and the OpenAI *API*, both exclude API inputs from training).
- Never point safety/memory/embeddings at a free prompt-reselling route.

This is why the table above splits "beats (free)" from "safety+memory (paid)" even though both are Gemini.

---

## 4. Configuration — the exact switches

Set in `.env` (never commit it — it's gitignored). Values are the pilot targets.

```dotenv
# --- Provider routing ---
AI_PROVIDER_ORDER=gemini            # (see note) or route Gemini via openrouter
AI_BEAT_MODEL=<gemini-flash-slug>            # FREE tier — beats only
AI_SAFETY_MODEL=<gemini-flash-lite-slug>     # PAID/data-excluded — must differ from beat model
# AI_MEMORY_MODEL inherits AI_SAFETY_MODEL

# --- Embeddings (semantic recall) ---
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=<key>                 # pennies; or drop to local-hash for $0 (weaker recall)

# --- Images ---
IMAGE_PROVIDER=cloudflare
CLOUDFLARE_ACCOUNT_ID=<id>
CLOUDFLARE_API_TOKEN=<token>
IMAGE_MODERATION=auto                # vision moderation on

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

> **Router note:** if the app's `AI_PROVIDER_ORDER` doesn't yet have a native `gemini` provider implemented, route Gemini **through OpenRouter** (OpenAI-compatible) as an interim — set `AI_PROVIDER_ORDER=openrouter` with Gemini slugs. Confirm which is wired before launch; Django refuses to boot on a provider/model-form mismatch. **The exact model slugs must be filled in from each provider's current model list at setup time.**

> **Boot guards to expect (all correct, don't disable):** `prod.py` refuses to start with the safety classifier off, with `IMAGE_MODERATION=off`, or with `MEDIA_STORAGE=local`. These are safety rails, not bugs.

---

## 5. Cost controls — so we never get surprised

Non-negotiable at a tight budget:

1. **GCP billing budget + alert.** Set a budget on the billing account with email alerts at **$50 / $150 / $250** of the $300, so the credit can't silently drain. (Note: GCP alerts *notify*, they don't hard-stop — see #4.)
2. **Keep beats on the free Gemini tier.** The credit should only be touched by the small paid safety/memory calls and the VM. If beat volume ever approaches the free RPM cap, that's a signal to revisit, not to silently overspend.
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
| **Pilot** | 15 | GCP VM on $300 credit | Gemini Flash free + Flash-Lite paid | ≈$0 out of pocket |
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

1. **VM region** — defaulted to nearest your users (`asia-south1`/Mumbai if India-based). *Confirm your users' location.*
2. **Embeddings** — recommend OpenAI `text-embedding-3-small` (pennies) over free `local-hash`, because semantic recall is the moat. `local-hash` is $0 but lexical; fine only if you want zero extra vendors at launch.
3. **Day-90 plan** — assumed migrate to Hetzner. Alternative: stay on GCP paid (simpler, but ~5× the compute cost).
4. **Native Gemini provider vs OpenRouter** — confirm which is wired in the router before launch (§4 note).
5. **Domain** — do we want a custom pilot URL, or is the VM IP / a free subdomain fine for 15 users?

---

## 12. Accounts to create / keys to obtain (checklist)

- [ ] Google Cloud account + project + billing enabled (unlocks $300 credit, TTS, Gemini paid)
- [ ] Gemini API key — free tier (beats)
- [ ] Gemini API — paid/data-excluded access for safety + memory (Vertex or paid AI Studio)
- [ ] Google Cloud Text-to-Speech API enabled + key
- [ ] OpenAI account + API key (embeddings) — with a low usage cap
- [ ] Cloudflare account → Workers AI token + Account ID (images)
- [ ] Cloudflare R2 bucket + access keys + public/CDN URL (media)
- [ ] ElevenLabs account + API key + a voice ID (voice failover)
- [ ] (Day 90) Hetzner account
- [ ] Budget alerts configured on GCP and OpenAI **before** onboarding users
