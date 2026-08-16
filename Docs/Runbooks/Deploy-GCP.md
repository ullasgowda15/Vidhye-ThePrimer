# Runbook — Deploy to GCP (closed alpha, real keys)

> **What this is:** the concrete, command-by-command GCP deploy that glues together the three docs you already have — `DEPLOY.md` (env vars + the same-site constraint), `docs/Hosting.md` (the vendor plan and the day-90 migration), and `docs/Runbooks/Launch.md` (the launch sequence once the server is up). **Do the deploy here, then hand off to `Launch.md` at Step 12.**
>
> **How we run it:** I (Claude) can't hold your GCP login, enter billing, or store your real API keys — so you drive the sensitive steps (log in, pay, paste secrets) and I walk you through every command and debug live. Nothing here needs a test/free setup; these are the **real** services from `Hosting.md`.
>
> **Before you start, read once:** `DEPLOY.md` → the section "The API and the PWA must be same-site". It's a hard constraint, and it decides your domains (Step 0). Getting it wrong makes parent logins silently break.

---

## Step 0 — Decide domains (the same-site constraint)

The parent session rides a refresh cookie with `SameSite=Lax`, which is the CSRF control on `/api/v1/auth/refresh`. The PWA and the API **must be same-site**. Two workable shapes:

- **One origin (simplest):** serve both the PWA and the API from `https://app.theprimer.xyz`, with the reverse proxy routing `/api/*` to Django and everything else to the built PWA. Recommended for the alpha.
- **Same registrable domain:** `app.theprimer.xyz` (PWA) + `api.theprimer.xyz` (API). Also fine — both are `theprimer.xyz`.

**Not OK:** the PWA on a Vercel/Netlify domain and the API on GCP — that's cross-site, and refresh will fail.

⬜ Domain chosen. ⬜ You own it (any registrar; ~$1/mo). We'll point DNS at the VM in Step 6.

---

## Step 1 — GCP project, billing, budget alerts (you drive)

1. Create a GCP account and a **new project** (e.g. `the-primer`).
2. **Enable billing** — this unlocks the **$300 free credit**, and also Text-to-Speech and Vertex/Gemini paid. (Card verification only; you won't be charged inside free tiers — see `Hosting.md §5`.)
3. **Budget + alerts:** Billing → Budgets → create a budget with email alerts at **$50 / $150 / $250** of the credit. (Alerts notify; they don't hard-stop. Ask me to add the optional hard-stop function if you want a true ceiling.)
4. Enable APIs: **Compute Engine**, **Cloud Text-to-Speech**, and **Vertex AI** (for the paid Gemini calls — beats, safety, memory *and* image moderation, all paid/data-excluded per `Hosting.md §3`).
5. **Service account for Vertex.** IAM & Admin → Service Accounts → create one (e.g. `primer-vertex`) → grant it **`roles/aiplatform.user`** → Keys → Add key → JSON. This one credential covers all four model calls.
   - **Where the file goes:** onto the server at `/home/primer/.config/primer/vertex-sa.json`, `chmod 600`, owned by the app user, mounted into the container. **Never inside the repository** — `.gitignore` has patterns for the obvious names, but they only catch the names somebody thought of, and one of these has already turned up at a repo root as `gemini-key.json` where `*.pem`/`*.key` did not match it.
   - **Never email it, put it in a chat, or leave it in a cloud-synced folder** (Downloads, OneDrive, Drive). If it has been in any of those, rotate it: create a new key, update the file, delete the old key. That is two minutes and it is the only remediation that actually works.

⬜ Project created, billing on, budget alerts set, APIs enabled, service-account key downloaded and stored outside any repo.

---

## Step 2 — Create the VM (you drive; I give exact values)

Compute Engine → Create instance:

- **Name:** `primer-alpha`
- **Region:** nearest your users — `asia-south1` (Mumbai) if India-based. *(Confirm.)*
- **Machine type:** `e2-medium` (2 vCPU, 4 GB). Enough for Postgres+pgvector, Redis, Django, Celery at pilot scale.
- **Boot disk:** Ubuntu 22.04 LTS, 30 GB.
- **Firewall:** allow HTTP + HTTPS.

After creation, reserve a **static external IP** (VPC → IP addresses → promote the ephemeral IP) so DNS doesn't break on reboot.

⬜ VM running, static IP noted.

---

## Step 3 — Harden the box (you run over SSH; I provide the commands)

SSH in (browser SSH from the console is fine), then:

```bash
sudo adduser primer && sudo usermod -aG sudo primer         # non-root user
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
sudo apt update && sudo apt -y install unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades
# SSH keys only: put your public key in /home/primer/.ssh/authorized_keys, then disable password auth
```

⬜ Non-root user, firewall on (22/80/443), SSH-key-only, auto-updates on.

---

## Step 4 — Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker primer      # log out/in so it takes effect
docker compose version              # confirm the compose plugin is present
```

⬜ `docker` and `docker compose` work as the `primer` user.

---

## Step 5 — Cloudflare R2 (media storage) + Workers AI (images)

1. Cloudflare dashboard → **R2** → create a bucket (e.g. `primer-media`).
2. Create an **R2 API token** (Object Read & Write) → note the access key, secret, and the S3 endpoint `https://<accountid>.r2.cloudflarestorage.com`.
3. Give the bucket a **public URL** or a custom domain (`media.theprimer.xyz`) → that's `MEDIA_CDN_BASE_URL`.
4. Turn on **bucket versioning** (Launch.md §2 — a restored DB with an empty bucket is a child's history pointing at nothing).
5. **Workers AI:** note your **Account ID** and create an API token with Workers AI access (images).

⬜ R2 bucket + keys + public/CDN URL; versioning on; Workers AI token + account id.

---

## Step 6 — DNS + reverse proxy with auto-HTTPS

1. At your registrar/Cloudflare DNS, point your domain(s) (Step 0) at the VM's static IP (A record). If you chose the one-origin shape, one `app` record; if split, `app` + `api`.
2. On the VM, run **Caddy** as the TLS-terminating reverse proxy (auto-HTTPS, no certbot). A minimal `Caddyfile` for the one-origin shape:

```
app.theprimer.xyz {
    handle /api/* {
        reverse_proxy 127.0.0.1:8000     # Django uvicorn
    }
    handle /admax* {
        reverse_proxy 127.0.0.1:8000     # the ops dashboard (two-factor)
    }
    handle {
        root * /srv/primer/frontend/dist  # built PWA
        try_files {path} /index.html
        file_server
    }
}
```

> **`/admax` needs its own `handle` block.** The catch-all below sends anything
> unmatched to `try_files {path} /index.html`, so without it `/admax` reaches the
> built PWA and renders the parent app's shell — Django never sees the request, and
> the response is a React Router 404 carrying **HTTP 200**, which hides the problem
> from any check that looks at the status code.
>
> **Correction, 2026-08-16.** An earlier version of this note claimed nothing
> forwards `/admin` in production and that the admin was therefore unreachable.
> That was inferred from the example above rather than read off the box, and it is
> wrong: the deployed Caddyfile *does* proxy `/admin/*`. The Django admin is
> reachable; it renders unstyled because no `collectstatic` runs, which is the
> whole of "the admin isn't visible in production". The real config is recorded in
> `Production-Facts.md`, and the exposure is assessed in `../Security-Notes.md` —
> `infra/admax-setup.sh --close-admin` closes it if that is wanted.

> Caddy sets `X-Forwarded-Proto`, which `prod.py`'s `SECURE_PROXY_SSL_HEADER` expects — so `SECURE_SSL_REDIRECT=1` works and cookies are marked secure. Add Caddy to your `docker-compose` or run it on the host; ask me and I'll wire whichever fits `infra/`.

⬜ DNS resolves to the VM; HTTPS serves with a valid cert.

---

## Step 7 — Assemble the production `.env` (you paste real secrets)

Start from `DEPLOY.md`'s env table (the full prod list) and `Hosting.md §4` (the provider switches). The variables with **no safe default** — the boot will refuse without them (`prod.py`):

| Variable | Value | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` | |
| `SECRET_KEY` | fresh 50+ random chars | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `MEMORY_ENCRYPTION_KEY` | generated once | **Store it where the DB backups are NOT.** Rotating `SECRET_KEY` would orphan every memory — this key is separate for that reason. |
| `INFERENCE_BUDGET_USD_PER_WINDOW` | your spend cap | Size it after a week of dogfooding (`Launch.md §1`): cost/session × sessions/window × 2. For launch, set a deliberately safe number now and tune later. `prod.py` won't boot on `0`. |
| `DATABASE_URL` / `REDIS_URL` | the compose Postgres/Redis | pgvector must be enabled (the first migration does it; the role must permit `CREATE EXTENSION`). |
| `ALLOWED_HOSTS` / `CORS_ALLOWED_ORIGINS` | your domain(s) | |
| `MEDIA_STORAGE=s3` + `AWS_*` + `AWS_S3_ENDPOINT_URL` + `MEDIA_CDN_BASE_URL` | R2 from Step 5 | `prod.py` refuses `local`. |
| `IMAGE_PROVIDER=cloudflare` + `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | Step 5 | never `local`. |
| `TTS_PROVIDER=google` + `GOOGLE_TTS_API_KEY` | Step 1 | `TTS_FALLBACK_PROVIDERS=elevenlabs` + `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`. Never add `local`. |
| `EMBEDDING_PROVIDER=google` + `EMBEDDING_MODEL=gemini-embedding-001` + `EMBEDDING_DIMENSIONS=1536` | the same Vertex service account | Semantic recall — the moat. **No OpenAI account needed.** 1536 is a prefix of the model's native 3072 and is what keeps `memory.embedding` and its HNSW index unchanged; do not change it without a migration and a re-embed. |
| **LLM (beats/safety/memory)** | `AI_PROVIDER_ORDER=google`, `AI_BEAT_MODEL=gemini-2.5-flash`, `AI_SAFETY_MODEL=gemini-2.5-flash-lite`, `AI_MEMORY_MODEL=gemini-2.5-flash-lite` | The router has a **native `google` provider** (Vertex AI). The registered name is `google`, **not `gemini`** — Django refuses to boot on an unknown provider. Bare Vertex model ids, never gateway slugs. Set `AI_MEMORY_MODEL` explicitly rather than letting it inherit. All three are paid + data-excluded (§13.8). |
| **Vertex auth** | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_APPLICATION_CREDENTIALS=/home/primer/.config/primer/vertex-sa.json` | A **service account**, never an API key — that is what bills the call to this project and keeps it under the terms that exclude training. `prod.py` refuses to boot if any is missing or the file is not there. **`asia-south1` will not work:** it does not serve `gemini-2.5-flash-lite`, so the safety grader would 404 on every call (`Hosting.md §4`). |
| **Image moderation** | `IMAGE_MODERATION=auto`, `IMAGE_MODERATION_PROVIDER=google`, `IMAGE_MODERATION_MODEL=gemini-2.5-flash-lite` | The §13.8 gate, on the same Vertex credentials — no Anthropic key needed. Leave `IMAGE_MODERATION_PROVIDER` unset on an all-Google box and the gate silently degrades to structural-only while still reporting `auto`. Confirm from the logs that scans say `moderation=passed`, not `moderation=absent`. |
| `SECURE_SSL_REDIRECT=1`, `DEBUG=0` | | |

⬜ `.env` assembled on the server with real values, **not committed**, permissions `600`.

> **Privacy rule (don't skip):** beats, safety, memory **and** embeddings all run on paid/data-excluded routes. Never a free prompt-reselling route for any of them — on OpenRouter that means never a `:free` suffix, and set the account's data policy to disallow prompt logging/training as well.
>
> This changed after the Day 1–15 audit (F-3). The earlier version of this line said beats could use the free tier; that was wrong on the facts — a beat prompt carries the child's first name, age band, avoid list, fears and retrieved memories — and it contradicted the disclosure parents actually read. `Hosting.md §3` is the full reasoning.
>
> It is now enforced rather than remembered: `prod.py` refuses to boot on a `:free` child-data model, `manage.py launch_check` blocks on one (`privacy.provider_terms`), and the `ai.beat_model` rollback flag rejects one. If the boot fails here with `ImproperlyConfigured` naming a model, that guard is working — drop the `:free` suffix, do not disable the check.

---

## Step 8 — Bring it up

```bash
# clone the repo to /srv/primer, put .env in place, then:
make up           # Postgres (pgvector) + Redis
make migrate      # applies all 15 days' migrations; enables pgvector
make superuser    # your admin login
npm --prefix frontend ci && npm --prefix frontend run build   # PWA → frontend/dist (served by Caddy)
# start web + worker (via compose or systemd):
#   web:    uvicorn config.asgi:application --host 0.0.0.0 --port 8000   (MUST be ASGI — SSE)
#   worker: celery -A config worker -l info                             (NOT optional — art + voice)
```

⬜ Web + worker running; PWA built and served; `make ping` returns (worker live).

---

## Step 9 — Preflight (the gate before a child)

```bash
python manage.py launch_check --strict     # MUST exit 0
```

Fix every blocker it names — budget, encryption key, media storage, restore-drill. `--strict` refuses on warnings too, which is the point. Do not proceed past a non-zero exit.

⬜ `launch_check --strict` exits 0.

---

## Step 10 — Turn on the provider-dashboard items (`Launch.md §2`)

These live in dashboards, not code:

- ⬜ **Postgres PITR** / WAL archiving, retention ≥ 7 days.
- ⬜ **The dump cron** — `infra/backup/backup.sh`, daily, to somewhere that is **not** the DB provider (e.g. a second R2 bucket). *(This is the `Hosting.md` "top pre-family task" — do it now.)*
- ⬜ **Alert on `budget.tripped`** in the log stream.
- ⬜ **Alert on `safety.incident`** at high/critical severity.
- ⬜ **GitHub `production` environment** (required by `deploy.yml`'s `release`), with you as required reviewer.

---

## Step 11 — Run the drills against production, once

```bash
sh infra/backup/restore-drill.sh                                  # against the real managed dump
PRIMER_EVAL_FORCE_REGRESSION=cliffhanger_close pytest evals/ -q   # must go RED (proves the gate bites)
```

The restore drill against **managed** Postgres is the one that surfaces provider friction (role ownership, the `vector` extension) — and it must happen **before** the first family. Also push a release tag with a forced regression once to record the CI **red** row where `release` is skipped (`Launch.md`, Eval-Gate.md).

⬜ Restore drill passes on managed PG (and fails on a wrong key). ⬜ Eval gate goes red on a forced regression. ⬜ CI red row recorded.

---

## Step 12 — Production manual test → then hand off to Launch.md

Run **Part M** of `docs/Test/manual_testing.md` on production (infra checks + one full happy-path play + the **day-two greeting observed live**). Then read `manage.py metrics` and size the budget breaker for real.

When Part M passes, you're provisioned. **Continue from `docs/Runbooks/Launch.md §5` (onboard 10–25 families) and §6 (watch, daily).** Before any family, clear `docs/Launch/Pre-Family-and-YC.md`.

---

## Rollback / kill switches (know these before you need them)

- **Model rollback, no deploy:** `make flags ARGS="set ai.beat_model <id>"`.
- **Prompt rollback:** `make prompts ARGS="rollback system_identity <version>"`.
- **Spend runaway:** the budget breaker trips automatically → children get warm cached/fallback content; you get the `budget.tripped` alert.
- **Bad release:** the `production` environment gate + `needs: [gate]` means a red eval never deploys; to pull a live release, redeploy the previous tag.

## Day-90 note

The $300 credit is a ~90-day runway. Around day 75–80, migrate compute to Hetzner (~$5/mo) per `Hosting.md §8` — trivial because Postgres/Redis are your own containers and media is on R2, so only the DB dump moves.
