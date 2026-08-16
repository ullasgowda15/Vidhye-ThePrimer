# Deploying The Primer

Local development is covered in [README.md](README.md). This is the staging path.

Everything here that can be prepared in code **is** prepared. What remains needs
founder credentials — a cloud account, a Stripe key, a Vertex AI service account —
so it is written as a checklist rather than automated.

---

## What "deployable" means today

Day 1 ships a spine, not a product. Deployable means:

- `make up && make migrate && make run && make front` works end to end locally
- `docker build -f infra/Dockerfile.backend .` produces a working image
- CI is green on the pushed commit

There is no auth, no story loop, and no billing yet, so **staging must not be
publicly linked** until the safety layer lands (Day 7).

CI must be green before any of this is worth doing, and CI cannot run until the repo
has a remote — see **[Enable CI](README.md#enable-ci)** in the README for that
one-time step.

## Architecture on a host

Three processes and two managed services:

| Process | Command | Notes |
|---|---|---|
| Web | `uvicorn config.asgi:application --host 0.0.0.0 --port $PORT` | **Must be ASGI.** SSE streaming (§16.4) does not work under WSGI. |
| Worker | `celery -A config worker -l info` | Same image, different command. **Not optional.** Companion art and every beat's illustration and voice line are produced here — a deployment without a running worker tells stories with no pictures and no narration. |
| Static PWA | `frontend/dist/` on a CDN or static host | `npm run build` output |

| Service | Notes |
|---|---|
| PostgreSQL 16 | The **pgvector extension must be available.** Managed Postgres on Render/Railway/Fly/RDS all support it; enabling it is the first migration's job, but the instance must permit `CREATE EXTENSION`. |
| Redis | Celery broker. `django-db` is the result backend, so Redis holds no durable state. |

## Environment variables

`.env.example` lists the local set. Staging additionally needs:

| Variable | Value |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `SECRET_KEY` | A fresh random 50+ char value. **No default in prod — a missing key fails the boot on purpose.** |
| `DEBUG` | `0` |
| `DATABASE_URL` | From the managed Postgres |
| `REDIS_URL` | From the managed Redis |
| `ALLOWED_HOSTS` | Your staging hostname(s), comma-separated |
| `CORS_ALLOWED_ORIGINS` | The PWA's origin, comma-separated |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` | The Vertex AI service account. `global` for the location — **`asia-south1` does not serve `gemini-2.5-flash-lite`**, so the safety grader would 404 on every call. The JSON key goes on the box **outside the repo**, `chmod 600`; `prod.py` refuses to boot if it is missing or the path is not a file. |
| `AI_PROVIDER_ORDER` | `google` — the registered provider name, **not** `gemini`. Anthropic and OpenRouter remain selectable; don't ship a free model to a child. |
| `AI_BEAT_MODEL`, `AI_SAFETY_MODEL`, `AI_MEMORY_MODEL` | `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-flash-lite`. Bare Vertex ids, never gateway slugs. The grader must differ from the writer (§13.3) and the memory model must be set explicitly rather than inherited. |
| `IMAGE_MODERATION`, `IMAGE_MODERATION_PROVIDER`, `IMAGE_MODERATION_MODEL` | `auto`, `google`, `gemini-2.5-flash-lite` — the §13.8 vision gate on the same credential. Leave the provider unset on an all-Google box and the gate silently degrades to structural-only. |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | `google`, `gemini-embedding-001`, `1536`. Same credential; **no OpenAI account needed**. Do not change the width without a column migration and a re-embed. |
| `IMAGE_PROVIDER` | `cloudflare`. Never `local` outside development — that ships placeholder art to a child. |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | From the Cloudflare dashboard (Workers AI) |
| `TTS_PROVIDER` | `elevenlabs` |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | From the ElevenLabs console. The voice id is required — there is no vendor default. |
| `TTS_FALLBACK_PROVIDERS` | `google`, and set `GOOGLE_TTS_API_KEY` with it. **Effectively required:** the voice is what a pre-reader has instead of the words on screen, so a single TTS outage is a screen they cannot read (§4.9). Never add `local` — a chime would hand the child their turn and tell them nothing. |
| `GOOGLE_TTS_API_KEY` | An API key from a Google Cloud project with the Text-to-Speech API enabled. `GOOGLE_TTS_VOICE` defaults to `en-US-Neural2-F`. |
| `MEDIA_STORAGE` | `s3`. **`prod.py` refuses to boot without it**: containers are ephemeral, so local disk would lose a child's illustrations on every restart. |
| `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | The media bucket. Add `AWS_S3_ENDPOINT_URL` for R2/B2/MinIO. |
| `MEDIA_CDN_BASE_URL` | The CDN hostname in front of the bucket. Without it, media URLs point at the bucket directly — correct, but uncached and slower than the child experience needs. |
| `SECURE_SSL_REDIRECT` | `1` unless the platform already redirects |

`config/settings/prod.py` turns on HSTS, secure cookies, and
`SECURE_PROXY_SSL_HEADER`, which assumes TLS terminates at a proxy in front of the
app. That is true on Render, Railway, Fly, and behind an ALB.

## The API and the PWA must be same-site

**This is a deployment constraint, not a preference.** The parent session is
carried by a refresh cookie with `SameSite=Lax`, and that attribute is the
load-bearing CSRF control on `/api/v1/auth/refresh` — an endpoint that is
unauthenticated by design, because the cookie *is* the credential
(`backend/apps/accounts/cookies.py`).

`Lax` means the browser withholds the cookie from **cross-site** requests. So the
API and the app have to be same-site:

| Layout | Works | Why |
|---|---|---|
| `theprimer.app` + `api.theprimer.app` | ✅ | Same registrable domain — this is the intended shape |
| One host, API under `/api` | ✅ | Same origin; nothing is cross-site |
| `theprimer.app` + `primer-api.onrender.com` | ❌ | Different sites. The cookie is never sent, so **every reload signs the parent out** |

What that costs if it is got wrong is not an error message: a child cold-starting
the app lands at a login form they cannot pass (§14.5). Point a custom domain at
the API service before shipping; do not rely on the platform's default hostname.

Locally this is already true — the Vite dev proxy makes `/api` same-origin — which
is exactly why the mistake does not show up until the first deploy.

Two related settings, for completeness:

- `REFRESH_COOKIE_SECURE` is `True` in `prod.py`. A `Secure` cookie needs HTTPS
  end-to-end from the browser's point of view; that is what
  `SECURE_PROXY_SSL_HEADER` above establishes.
- `CORS_ALLOWED_ORIGINS` must list the PWA's exact origin. `CORS_ALLOW_CREDENTIALS`
  is on, and a wildcard combined with credentials is a credential-exposure surface
  — `prod.py` reads the list from the environment for that reason, and `dev.py` is
  scoped to localhost.

---

## Founder checklist

### 1. Provision

- [ ] Create the project/app on the chosen host (Render, Railway, or Fly)
- [ ] Add managed **PostgreSQL 16** and confirm `pgvector` can be created
- [ ] Add managed **Redis**
- [ ] Note the internal connection strings

### 2. Configure

- [ ] Set every variable in the table above on **both** the web and worker services
- [ ] Generate `SECRET_KEY`:
      `python -c "import secrets; print(secrets.token_urlsafe(64))"`

### 3. Deploy the backend

- [ ] Point the service at `infra/Dockerfile.backend` with the repo root as build context
- [ ] Web service start command:
      `uvicorn config.asgi:application --host 0.0.0.0 --port $PORT`
- [ ] Worker service start command: `celery -A config worker -l info`
- [ ] Release/pre-deploy command: `python manage.py migrate`
- [ ] Health check path: `/api/health/`

### 4. Deploy the PWA

- [ ] Build: `cd frontend && npm ci && npm run build`
- [ ] Publish `frontend/dist/` as a static site
- [ ] Set `VITE_API_BASE_URL` to the API origin **if** the PWA is on a different
      hostname. Same-hostname deploys need nothing — the client uses relative paths.
- [ ] Add a SPA rewrite (`/* -> /index.html`) so `/parent` doesn't 404 on refresh
- [ ] Route **`/admax*`** to Django alongside `/api/*`. The SPA rewrite above is a
      catch-all, so without this the ops dashboard resolves to the parent app's
      shell and Django never sees it. Do **not** add a route for `/admin` — leaving
      it unforwarded is what keeps the Django admin off the internet entirely
      (`Docs/Runbooks/Deploy-GCP.md` Step 6).

### 5. Verify

- [ ] `python manage.py launch_check --live --strict` on the deployed box. This is
      the one that catches a credential the vendor refuses; nothing else here does.
      The pilot shipped with `CLOUDFLARE_ACCOUNT_ID=YOUR_CLOUDFLARE_ACCOUNT_ID`,
      `ELEVENLABS_VOICE_ID=YOUR_ELEVENLABS_VOICE_ID` and a GCP project answering
      `403 CONSUMER_INVALID`, and every other check on this page passed. `--live`
      calls each vendor; the Gemini image probe draws one picture and costs cents.
- [ ] `curl https://<api-host>/api/health/` returns `{"status":"ok",...}`
- [ ] `/api/docs/` renders
- [ ] `python manage.py createsuperuser` (one-off shell)
- [ ] `python manage.py admax_enroll <that-email>` (same shell), then add the printed
      seed to an authenticator app. **The seed is shown once and is not
      recoverable** — if it is lost, re-run with `--reset`.
- [ ] Sign in at **`/admax`** — email + password, then the six-digit code — and
      confirm the metrics render. This is the ops dashboard in production; the
      Django admin at `/admin/` is *not* the way in, and is expected to look
      broken there because this image collects no static files (which is why
      `/admax` carries all of its own CSS inline).
- [ ] Worker logs show it connected to the broker
- [ ] The PWA loads and its health dot reads `ok`

### 6. Lock it down

- [ ] Staging is **not** publicly linked or indexed (`robots.txt`, or basic auth at
      the platform edge)
- [ ] Confirm automated Postgres backups are on — memory durability is a top SLO
      (§16.10); losing a child's history breaks the core promise
- [ ] Rotate any key that was ever pasted into a terminal or chat

---

## Not yet wired

Deliberately absent on Day 1, listed so nobody assumes they exist:

- **Object storage + CDN** for illustrations and audio (§16.6) — Day 6
- **Stripe** (§17.2 `subscription`) — Day 14
- **Runtime safety guard** on generated beats (§13.2) — Day 7. Until it lands,
  `beat.safety_status` is `unchecked` and staging must stay unlinked.
- **Observability**: OpenTelemetry, Sentry, LLM tracing (§16.1) — Day 14
- **Blue-green / rolling deploys and feature flags** (§16.8)
- **A staging→prod promotion path** — one environment is enough until there are users
