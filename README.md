# The Primer

An AI-generated living storybook — a personalized lifelong learning companion for children aged 3–7.

`docs/PRODUCT.md` is the authoritative product and technical spec. `docs/Build/` and
`docs/Review/` drive the 15-day build loop.

> **Status: the 15-day build is complete and on `main`; the product is not yet launched.**
> Days 1–15 are built, reviewed, fixed and CI-green — auth and consent, the companion,
> the story loop over SSE, the choice loop, memory (write, consolidation, semantic
> recall), the Learning Engine, the safety layer, the parent dashboard, analytics and
> cost instrumentation, and the eval ship gate. Since then, every route that carries
> child data moved onto **Gemini on Vertex AI** under one service account.
>
> **What is not done:** no production deployment and no family has played yet. See
> `Docs/PROGRESS.md` for the live state and `Docs/Runbooks/Launch.md` for what is owed
> before a child touches it.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · Django 5 + DRF, served under **ASGI (uvicorn)** |
| Jobs | Celery + Redis |
| Database | PostgreSQL 16 + pgvector |
| Frontend | React + Vite + Tailwind (PWA). Node is a build tool only — there is no Node server |
| AI | **Gemini on Vertex AI** as deployed — beats, safety grader, memory digest, image moderation and embeddings, all on one service account. Anthropic Claude and OpenRouter remain implemented providers, selectable by config; OpenAI is a fallback seam |
| Embeddings | `gemini-embedding-001` at 1536 dims on the same Vertex credential (pgvector + HNSW, cosine) |

The backend runs under ASGI rather than WSGI because story beats stream to the client
over SSE from async views (PRODUCT.md §16.4). Don't swap `make run` for a WSGI server.

## Layout

```
backend/    Django project — config/ (settings, asgi, celery) + apps/*
frontend/   React + Vite SPA/PWA
evals/      The ship gate (PRODUCT.md §19.7) — golden, red-team, age rubric, regression
infra/      docker-compose, Dockerfiles, backup + restore-drill scripts
Docs/       PRODUCT.md (source of truth) + Build/ + Review/ + Runbooks/
```

`evals/` is deliberately outside `backend/`: it is not a unit-test suite for the
backend, it is the gate that grades the *product's output*, and the day it also
grades the frontend's rendering of a beat it should not have to move. It has its
own pytest config, so `pytest evals/ -q` works from the repo root.

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
cp .env.example .env          # then point GOOGLE_APPLICATION_CREDENTIALS at a Vertex
                              # service-account JSON (kept OUTSIDE this repo) for hello-beat
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
| `smoke-media` | Live media smoke test — real Cloudflare + ElevenLabs calls. Never in CI |
| `smoke-story` | Live story smoke test: plan + beat + art + voice for one child. Never in CI |
| `render-companion` | Render a companion's reference set + 3 scenes |
| `redteam` | The safety red-team suite: injection, fear ceilings, unsafe bait |
| `evals` | **The ship gate** (§19.7): golden + red-team + age rubric + regression. No keys, no spend |
| `evals-live` | The same scenarios against the real provider, with a spot-check dump. Spends money |
| `launch-check` | Preflight a deployment before a child touches it (`STRICT=--strict` to refuse warnings) |
| `backup` / `restore-drill` | Dump the database; restore it into a scratch copy and prove the data came back |
| `flags` | Show or pull a feature flag — the no-deploy levers (§16.10) |
| `prompts` | Show or roll back a system prompt version (§19.2) |
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

**As deployed: `google` (Gemini on Vertex AI).** The registered provider name is
`google` — *not* `gemini`; Django refuses to boot on an unknown provider. Model ids
are bare Vertex ids (`gemini-2.5-flash`), never gateway slugs. Auth is a **service
account** (`GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION=global` +
`GOOGLE_APPLICATION_CREDENTIALS`), never an API key.

| `.env` | Result |
|---|---|
| `AI_PROVIDER_ORDER=google` + a Vertex service account | **Gemini on Vertex AI — the deployed primary** (beats, safety, memory; embeddings + image moderation share the same credential) |
| `AI_PROVIDER_ORDER=anthropic` + `ANTHROPIC_API_KEY` | Claude — an implemented alternative |
| `AI_PROVIDER_ORDER=openrouter,anthropic` + `OPENROUTER_API_KEY` | OpenRouter, falling back to Claude if its key is missing |

`AI_BEAT_MODEL` is provider-relative — a bare Gemini id for `google`, a Claude model
id for `anthropic`, an OpenRouter slug for `openrouter`.

**A child-data model may never be a training-permitted route (§13.8).** `prod.py`
refuses to boot, and `launch_check` blocks, on a `:free` OpenRouter suffix for
`AI_BEAT_MODEL` / `AI_SAFETY_MODEL` / `AI_MEMORY_MODEL` / `EMBEDDING_MODEL`. A `:free`
model is fine only for throwaway offline plumbing checks, never for a run that
touches a real child's data. On Vertex the equivalent is a service account against a
billed project, not an AI Studio key.

Providers that don't enforce `json_schema` natively (OpenRouter, some models) degrade
to JSON mode plus the router's validate-and-repair pass, so a beat still arrives.
The LLM provider is LLM-only. Images and voice have their own adapters, below.

### Illustrations, voice and embeddings

Three more swappable adapters, each chosen by one name in `.env`:

| Setting | Implemented | Offline alternative |
|---|---|---|
| `IMAGE_PROVIDER` | `cloudflare` — FLUX on Workers AI (`CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`) | `local` — deterministic placeholder art, no key, no spend |
| `TTS_PROVIDER` | `elevenlabs` (`ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`) | `local` — a short chime |
| `EMBEDDING_PROVIDER` | `google` — `gemini-embedding-001` at 1536 dims, on the **same Vertex service account** as the LLM routes. `openai` also works (`OPENAI_API_KEY`) and is no longer required | `local` — a *lexical* hashing trick, not a model |

**`local` embeddings will lie to you about the moat.** They make the vector column,
the HNSW index and the cosine ordering fully exercisable with no key and no spend,
which is why CI uses them — but they match on shared words, so they cannot connect
"the winged reptile he invented" to a memory about a dragon. Every consolidation run
logs which provider it used, so a database of lexical vectors is never mistaken for
a semantic one.

A named provider with missing credentials is an **error**, not a quiet downgrade
to placeholder art — that is the kind of failure nobody notices until a child is
looking at it. `local` is always an explicit choice.

**The voice fails over; the picture does not.** A beat is answerable when it can
be *heard*, so a missing illustration is a picture-less but playable scene — but a
missing voice is a screen a pre-reader cannot read with nothing to tap (§4.9).
`TTS_FALLBACK_PROVIDERS` (default `google`, needs `GOOGLE_TTS_API_KEY`) is tried
in order when the primary errors or runs out of quota, and every failover is
logged as `media.tts_failover`. Do not put `local` in that list: a chime would
land the voice stage, hand the child their turn, and tell them nothing.

Generated media is written to object storage and served from a CDN
(`MEDIA_STORAGE=s3`). In development `MEDIA_STORAGE=local` writes to
`backend/media/` and Django serves it while `DEBUG` is on.

To see whether a companion actually looks like itself across scenes:

```bash
make render-companion CHILD=<child-uuid>
```

It builds the reference set if it is missing, renders three ordinary story
scenes, prints where every file landed, and reports how many provider calls it
made. Run it twice — the second run should report **0 generated**, because the
`(style, character, scene)` cache answered everything.

## Smoke-testing the AI path

`make hello-beat` prints a beat for a human to read. `make smoke` **asserts** it:

```bash
make smoke                              # uses AI_BEAT_MODEL from .env
make smoke SMOKE_ARGS=--pick-free-model # offline-plumbing only — a free route may NOT carry child data (§13.8)
```

It runs against whichever provider is first in `AI_PROVIDER_ORDER`. As deployed that
is `google` (Vertex), so it uses the service-account credential from `.env`; to
smoke-test on another provider, put e.g. `AI_PROVIDER_ORDER=openrouter,anthropic` in
`.env` first. `--pick-free-model` is for throwaway plumbing checks only — the boot
guard forbids a `:free` route for any child-data model, so never use it for a run
against a real child's data.

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

## Smoke-testing the media path

```bash
make smoke-media
```

Needs `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`, `ELEVENLABS_API_KEY` +
`ELEVENLABS_VOICE_ID`, and `IMAGE_PROVIDER=cloudflare` / `TTS_PROVIDER=elevenlabs`.
Anything missing is named exactly, and nothing prints a key value.

The unit suite proves the *pipeline* against the deterministic offline adapter,
which is precisely the kind of test that cannot notice that the pictures are
placeholder SVGs or that nothing was ever spoken. This closes that gap.

| Check | What it proves |
|---|---|
| **A** real image | A real render came back: a raster type (`image/png`/`jpeg`/`webp`), above a 5KB floor, `provider=cloudflare`. **Fails explicitly on `image/svg+xml`** — that is the placeholder adapter's signature. |
| **B** real audio | The greeting is real audio above a 2KB floor from the `elevenlabs` adapter, and reports which voice the companion's voice key resolved to. |
| **C** cache hit | The same `(style, character, scene)` costs **one** provider call, then zero — asserted with a call counter, not inferred from timing. |
| **D** reference set | Portrait + the 5 canonical expressions + the greeting, `assets_status=ready`, exactly 7 `reference_asset_ids`, every URL servable and non-SVG. |
| **E** safety scan | Every `Media` row carries a real `safety_status` — nothing reached storage unscanned. |

Exit codes: **0** all passed · **1** a check failed · **2** not set up yet.

**Then open the contact sheet.** The run prints its path
(`backend/media/smoke/contact-sheet.html`). It is one page with every companion
image in a grid and a player for the greeting, because two things cannot be
asserted by a script:

- **Consistency** — is it recognisably the *same character* across the scenes?
  Expect some drift; free `flux-1-schnell` is text-only, so judge "good enough
  for dev", not perfect.
- **Voice** — does the greeting sound warm and right for a small child? If not,
  change `ELEVENLABS_VOICE_ID` and re-run with `SMOKE_ARGS=--fresh`.

**Credit-frugal.** It owns a dedicated fixture (`smoke-media@primer.invalid` /
"Smokey") and reuses its reference set, so a repeat run costs **one** image — the
cache probe, which has to miss to prove anything. `SMOKE_ARGS=--fresh` drops the
fixture and pays for the whole set again; `SMOKE_ARGS="--child <uuid>"` runs
against a real companion instead (read-only — `--fresh` is refused there).

**Not in CI and not in `make test`.** It makes real, metered calls; `make test`
stays hermetic. Run it by hand after any change to the adapters, the art
direction, or the cache keys.

## Smoke-testing the story loop

```bash
make smoke-story CHILD=<child-uuid>
```

Generates one real adventure end to end: the plan and beat one from Claude, the
illustration and the narration from the media providers, running concurrently. It
calls exactly what `GET /api/v1/adventures/{id}/stream` calls; the only thing it
does differently is print.

It exists because the automated tests use a stand-in model, and a stand-in cannot
answer the two questions Day 4 is judged on:

- **Is beat one personalised?** The child's seeds and the generated beat are
  printed side by side. A frog-lover should get a frog.
- **Do the illustration and the voice really overlap?** With the offline adapters
  both finish in milliseconds and concurrency is unmeasurable. With real providers
  it is seconds: compare the printed wall time against the two per-stage durations
  in the `story.media.timing` log lines. Concurrency is working when the wall time
  is close to the *slower* of the two, not their sum.

The adventure is deleted again unless you pass `SMOKE_ARGS=--keep`. Run
`make render-companion CHILD=<uuid>` first, or the illustration will not be
on-model — the canonical visual descriptor is what holds a companion's look
together (§8.2).

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

## The safety layer

Every generation traverses `backend/apps/safety/` (§13.2). Nothing reaches a child
around it.

```
inputs ──► guard_input ──► assemble + generate ──► guard_output ──► image scan ──► child
              │                                          │                │
              ▼                                          ▼                ▼
        incident row                      rewrite / fallback / escalate   stock art
```

| Stage | Module | What it does |
|---|---|---|
| Input guard | `scrub.py`, `pipeline.guard_input` | Strips injection shapes, PII and layer-tag forgeries from everything a parent, a child or an earlier model wrote (§13.5) |
| Output guard | `policy.py` + `classifier.py` | A deterministic policy check that always runs, plus an **independent** model call on a **different model** (`AI_SAFETY_MODEL`) — never the generator grading itself (§13.3) |
| Fallback | `fallbacks.py` | Hand-written, human-reviewed warm beats, illustrated and voiced through the normal pipeline. A child cannot tell one from a generated beat |
| Image gate | `apps/media/moderation.py`, `apps/media/stock.py` | A vision moderation call before any byte is stored; a refusal becomes hand-drawn stock art, never a missing picture (§13.8) |
| Record | `incidents.py`, `escalation.py` | Every non-pass writes a `SafetyIncident` (`/admin/safety/safetyincident/`, filterable by severity); a distress disclosure fires the parent-notify seam (§13.10) |

Two commitments worth knowing before you change anything here:

- **A guard that could not run is never a pass.** With no credentials the model
  classifier fails, the deterministic policy stands, and an incident records the
  degradation. `prod.py` refuses to boot with either guard switched off.
- **Incidents carry rule names, never text.** The table records *what fired*, not the
  sentence that fired it — it must not become a second copy of what we refused to show
  anyone (§13.8).

```bash
make redteam    # pytest apps/safety/redteam -q
```

The corpus (`apps/safety/redteam/corpus.py`) is the specification and the tests are only
how it is checked. When you find something that gets through, **add it to the corpus
first**, watch the test fail, then fix the guard — an entry added after the fix never
proved anything. Half the corpus is benign story text, because a filter that blocks
everything is trivially safe and ships nothing.

## Prompt assembly

`backend/apps/safety/prompting/` turns a child into the ordered, labeled prompt layers of
§19.1. It is pure and deterministic — the only I/O is reading the active `PromptVersion`
row.

Three properties are load-bearing and covered by tests: the safety layer carries the
child's `intensity_ceiling` and every `avoid_topic` and phrases guidance by age band;
memory and child input render as `kind="data"` blocks with an explicit "never obey this"
preamble (§13.5); and **every untrusted string is scrubbed on the way in**, including the
profile fields that reach an *instruction* layer, which the data fence never protected.

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

## Runbooks

Three, in `Docs/Runbooks/`, and each exists because the thing it describes is done
under pressure by somebody who should not be reading source at the time:

| | For |
|---|---|
| `Eval-Gate.md` | Running the gate, **proving it blocks a deploy**, changing a threshold, adding a case |
| `Backup-Restore.md` | Taking a backup, the restore drill (and its log), and what differs when it is a real restore |
| `Launch.md` | The closed-alpha sequence, what to watch daily, and the three stop-the-line conditions |

---

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
