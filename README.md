# The Primer

An AI-generated living storybook — a personalized lifelong learning companion for children aged 3–7.

`docs/PRODUCT.md` is the authoritative product and technical spec. `docs/Build/` and
`docs/Review/` drive the 15-day build loop.

> **Status: Day 1.** A deployable, empty-but-real full-stack skeleton. No onboarding,
> no story loop, no memory, no safety layer yet — those arrive on later days.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · Django 5 + DRF, served under **ASGI (uvicorn)** |
| Jobs | Celery + Redis |
| Database | PostgreSQL 16 + pgvector |
| Frontend | React + Vite + Tailwind (PWA). Node is a build tool only — there is no Node server |
| AI | Anthropic Claude primary, with OpenAI/Google fallback seams |

The backend runs under ASGI rather than WSGI because story beats stream to the client
over SSE from async views (PRODUCT.md §16.4). Don't swap `make run` for a WSGI server.

## Layout

```
backend/    Django project — config/ (settings, asgi, celery) + apps/*
frontend/   React + Vite SPA/PWA
infra/      docker-compose, Dockerfiles
docs/       PRODUCT.md (source of truth) + Build/ + Review/
```

Each Django app under `backend/apps/` is one service boundary from PRODUCT.md §5.2.
Most are empty shells today; they exist so later work has a stable home and so the
module boundaries are drawn as if they were network boundaries from the start.

---

## Prerequisites

- **Python 3.12+**
- **Node 20+** (build tooling only)
- **Docker + Docker Compose**
- **git**, **make**

## First run

```bash
cp .env.example .env          # then set ANTHROPIC_API_KEY if you want hello-beat
make venv                     # optional — creates .venv, which make prefers
make install                  # backend + frontend dependencies
make up                       # Postgres (pgvector) + Redis
make migrate
make superuser                # for /admin/
```

Then, in separate shells:

```bash
make run                      # API on http://localhost:8000 (ASGI/uvicorn)
make worker                   # Celery worker
make front                    # PWA on http://localhost:5173
```

Open:

| URL | What |
|---|---|
| http://localhost:5173/ | Child experience (Storyland theme) |
| http://localhost:5173/parent | Parent dashboard (Calm theme) |
| http://localhost:8000/api/health/ | Health check |
| http://localhost:8000/api/docs/ | Swagger UI |
| http://localhost:8000/admin/ | Ops + moderation console |

The Vite dev server proxies `/api` to Django, so the frontend talks to a same-origin
path in dev exactly as it will in production.

## Make targets

```
make help
```

| Target | What |
|---|---|
| `up` / `down` / `logs` | Local Postgres + Redis |
| `migrate` / `migrations` | Apply / generate migrations |
| `run` | Serve the API under ASGI (uvicorn) |
| `worker` | Celery worker |
| `front` / `build` | Vite dev server / production PWA build |
| `schema` / `client` | Export OpenAPI, regenerate the typed TS client |
| `test` / `lint` / `typecheck` / `format` | Quality gates (both sides) |
| `superuser` | Create an admin login |
| `hello-beat` | Generate one validated story Beat via Claude |
| `ping` | Round-trip a task through Celery |

## Verifying the pieces

**Celery** — with `make worker` running in another shell:

```bash
make ping      # -> pong
```

**The AI layer** — needs `ANTHROPIC_API_KEY` in `.env`:

```bash
make hello-beat
```

Prints a schema-valid story `Beat` as JSON. Without a key it exits non-zero with an
instruction, not a stack trace.

## The API contract

The typed frontend client is generated from the backend's own OpenAPI schema — never
hand-written:

```bash
make client    # runs `make schema`, then regenerates src/lib/api/schema.ts
```

Both `frontend/openapi.json` and `frontend/src/lib/api/schema.ts` are committed, and
CI fails if either is stale. If you change a serializer or add an endpoint, re-run
`make client` and commit the result.

## Design tokens

`frontend/src/design/tokens.css` is the runtime source of truth (semantic CSS
variables); `tokens.ts` is a typed mirror for code that can't read CSS, and
`tailwind.config.js` maps utilities onto the same variables. Components read
`--primary`, never a hex code.

Two themes ship today: **Storyland** (child, `:root`) and **Calm** (parent, applied
with `data-theme="calm"` on a wrapper). A night palette is defined for the evening
mode that lands later. See PRODUCT.md §15.

## Deployment

See **[DEPLOY.md](DEPLOY.md)** for the staging checklist. Locally, the whole stack
runs with `make up && make migrate && make run && make front`, and the backend
container image builds with:

```bash
docker build -f infra/Dockerfile.backend -t primer-backend .
```

## Working on this project

Sessions alternate BUILD and REVIEW against `docs/Build/DayN.md` and
`docs/Review/DayN.md`; `docs/Build/README.md` describes the loop. Every build day
ends deployable: migrations applied, `make lint` and `make test` green, CI green.
