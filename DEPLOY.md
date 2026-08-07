# Deploying The Primer

Local development is covered in [README.md](README.md). This is the staging path.

Everything here that can be prepared in code **is** prepared. What remains needs
founder credentials — a cloud account, a Stripe key, an Anthropic key — so it is
written as a checklist rather than automated.

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
| Worker | `celery -A config worker -l info` | Same image, different command |
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
| `ANTHROPIC_API_KEY` | From the Anthropic console |
| `AI_PROVIDER_ORDER` | `anthropic` on staging. OpenRouter is a dev convenience; don't ship a free model to a child. |
| `AI_BEAT_MODEL` | Optional — defaults to the Claude model in `base.py` |
| `SECURE_SSL_REDIRECT` | `1` unless the platform already redirects |

`config/settings/prod.py` turns on HSTS, secure cookies, and
`SECURE_PROXY_SSL_HEADER`, which assumes TLS terminates at a proxy in front of the
app. That is true on Render, Railway, Fly, and behind an ALB.

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

### 5. Verify

- [ ] `curl https://<api-host>/api/health/` returns `{"status":"ok",...}`
- [ ] `/api/docs/` renders
- [ ] `python manage.py createsuperuser` (one-off shell), then log into `/admin/`
- [ ] Create a Child + Companion in admin — no errors
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
- **Observability**: OpenTelemetry, Sentry, LLM tracing (§16.1) — Day 14
- **Blue-green / rolling deploys and feature flags** (§16.8)
- **A staging→prod promotion path** — one environment is enough until there are users
