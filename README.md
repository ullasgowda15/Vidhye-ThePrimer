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
| AI | Anthropic Claude primary; OpenRouter selectable for dev, with OpenAI/Google fallback seams |

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
cp .env.example .env          # then set a provider key if you want hello-beat
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

**The AI layer** — needs a provider key in `.env`:

```bash
make hello-beat
```

Prints a schema-valid story `Beat` as JSON. Without a key it exits non-zero with an
instruction, not a stack trace.

### Choosing a provider

`AI_PROVIDER_ORDER` decides which provider runs; the first one with credentials
wins. Nothing in the code changes when you switch.

| `.env` | Result |
|---|---|
| `AI_PROVIDER_ORDER=anthropic` + `ANTHROPIC_API_KEY` | Claude — the configured primary |
| `AI_PROVIDER_ORDER=openrouter,anthropic` + `OPENROUTER_API_KEY` | OpenRouter, falling back to Claude if its key is missing |

OpenRouter exists so the whole pipeline can be exercised on a **free** model without
an Anthropic key. Two things to know:

- `AI_BEAT_MODEL` is provider-relative — a Claude model id for `anthropic`, an
  OpenRouter model slug for `openrouter`. Free-model availability there changes over
  time, so pick one that is currently listed and supports JSON/tool output.
- Many free models don't support strict `json_schema` output. The provider asks for
  one, and on rejection degrades to JSON mode plus the router's validate-and-repair
  pass — so a beat still arrives, just less cheaply.

OpenRouter is LLM-only: images and TTS (Day 3) have their own providers.

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

## Enable CI

`.github/workflows/ci.yml` runs on every push to `main` and every PR, but a workflow
can't run until the repo has a remote. This is a one-time founder step:

```bash
gh repo create <owner>/primer --private --source=. --remote=origin   # or create it in the UI
git remote add origin git@github.com:<owner>/primer.git              # if you made it in the UI
git push -u origin main
```

Then confirm the run is green — `gh run watch`, or the **Actions** tab.

No secrets are needed. CI passes `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY` as
**empty** strings on purpose: it must never call a real provider, and every AI test
stubs the provider out. Adding a real key to repo secrets would let a bad test spend
money silently — don't.

Two things to know about the pipeline itself:

- **`mypy` is `continue-on-error: true`.** Type regressions will not fail the build
  while annotations are still being filled in. Read the step's output rather than
  trusting the green tick; flip it to blocking once it has been clean for a few days.
- **The three jobs** (`Backend`, `Frontend`, `Backend image`) run in parallel and
  include real smoke tests — ASGI over HTTP, a Celery `ping` round trip, and staleness
  gates on both generated artifacts (`frontend/openapi.json`, `src/lib/api/schema.ts`).
  A red staleness gate means you changed the API and didn't run `make client`.

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
