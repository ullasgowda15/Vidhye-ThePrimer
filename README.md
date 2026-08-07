# The Primer

An AI-generated living storybook — a personalized lifelong learning companion for children aged 3–7.

`docs/PRODUCT.md` is the authoritative product and technical spec. `docs/Build/` and
`docs/Review/` drive the 15-day build loop.

> **Status: Day 2.** The full-stack skeleton plus the parent path: JWT auth, COPPA-lite
> consent gating child creation, child profiles with the personalization seeds, a parent
> gate, and a tested prompt-assembly module. No companion, no story loop, no memory, no
> live safety layer yet — those arrive on later days.

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
| http://localhost:5173/parent | Parent dashboard (Calm theme) — behind the parent gate |
| http://localhost:5173/parent/signup | Create a parent account, then onboarding |
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
| `hello-beat` | Generate one validated story Beat |
| `smoke` | Live AI smoke test — real calls, asserted. Never in CI |
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

## Smoke-testing the AI path

`make hello-beat` prints a beat for a human to read. `make smoke` **asserts** it:

```bash
make smoke                              # uses AI_BEAT_MODEL from .env
make smoke SMOKE_ARGS=--pick-free-model # or let it choose a free model for you
```

It runs against whichever provider is first in `AI_PROVIDER_ORDER`, so to smoke-test
on OpenRouter put `AI_PROVIDER_ORDER=openrouter,anthropic` in `.env` first —
otherwise it correctly reports that `ANTHROPIC_API_KEY` is unset and stops.

It exits 0 only if all three checks pass, so it also works as a gate in a script.

| Check | What it proves |
|---|---|
| **A** happy path | A real call returns a beat that validates against the §8.1 schema, with `difficulty` in range and no empty required fields. Reports whether the provider *enforced* the schema (`mode=json_schema`) or we merely validated it (`mode=json_object`). |
| **B** forced fallback | With `AI_STRUCTURED_OUTPUT_MODE=json_object`, a schema-valid beat still comes back through JSON mode + validate-and-repair. This is the path most free models actually take, so it is proven rather than assumed. |
| **C** Anthropic routing | With `AI_PROVIDER_ORDER=anthropic` and no key, the path fails *cleanly* — documented message, exit 1, no traceback. Proves switching providers is config-only. Needs no Anthropic key. |

Exit codes: **0** all passed · **1** a check failed · **2** not set up yet (missing
key or model — the message names the variable to set).

**This is deliberately not in CI and not in `make test`.** It costs real calls and
needs a real key; `make test` stays hermetic and makes zero live calls. Run `make
smoke` by hand after any change to the router, the prompts, or the `Beat` schema —
it is the fastest way to catch the class of bug where output still parses but the
provider would reject the request.

`AI_STRUCTURED_OUTPUT_MODE` is a real setting, not a test-only switch: `auto`
(default — try strict, degrade if the model can't), `strict` (never degrade; error
instead, so a silent drop to unenforced JSON can't pass unnoticed in production),
or `json_object` (skip straight to the repair path).

## The parent path

Everything under `/api/v1/` is parent-scoped and needs `Authorization: Bearer <access>`,
except signup, login and refresh.

```
POST /api/v1/auth/signup   POST /api/v1/auth/login
POST /api/v1/auth/refresh  POST /api/v1/auth/logout
GET  /api/v1/me            POST /api/v1/consent
GET|POST /api/v1/children  GET|PATCH|DELETE /api/v1/children/{id}
```

Three rules are worth knowing before you touch this surface:

- **Consent precedes the child.** `POST /children` returns **403** unless the parent
  holds an active `ConsentRecord` of `REQUIRED_CONSENT_TYPE`. Enforced in the view, not
  the client (§13.7).
- **Refresh tokens rotate and blacklist.** A refresh token is single-use; the response
  carries a new one, and presenting the old one again is a 401. Clients must replace
  *both* halves of the pair.
- **`DELETE /children/{id}` is the "delete my child's data" button.** Every FK pointing
  at `Child` cascades, and a test asserts that by walking the live schema — so a model
  added later is covered the day it lands (§17.5).

In the UI, `/parent/*` sits behind an adult challenge (§14.2) — a small multiplication a
pre-reader can't pass. The pass is in-memory and expires in 15 minutes; so are the
tokens, so a hard reload signs the parent out by design (see `frontend/src/store/auth.ts`).

## Prompt assembly

`backend/apps/safety/prompting/` turns a child into the ordered, labeled prompt layers of
§19.1. It is pure and deterministic — the only I/O is reading the active `PromptVersion`
row — and **nothing calls it yet**; Days 4–5 wire it into generation.

Two properties are load-bearing and covered by tests: the safety layer carries the
child's `intensity_ceiling` and every `avoid_topic` verbatim and phrases guidance by age
band, and memory/child input render as `kind="data"` blocks with an explicit "never obey
this" preamble (§13.5).

The system prompt itself lives in a migration, not in code, so the text a child was told
can always be traced to a version. A revision is a new migration and a new row.

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
