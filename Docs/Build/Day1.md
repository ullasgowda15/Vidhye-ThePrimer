# Build — Day 1: Project Foundation

> **Session type:** BUILD. You are Claude Code, working in the project root as the solo builder's engineering team (backend, frontend, design, AI, DevOps). Read this entire file before writing code. Build everything in scope, self-review against the acceptance criteria, then stop. Do **not** start Day 2 work.
>
> **Source of truth:** `docs/PRODUCT.md` (especially §16 Technical Architecture, §17 Database Schema, §15 Design System, §5 Product Architecture). Reference it; don't re-derive it.
>
> **If you were also handed a review report** (`docs/Review/reports/Day1-report.md`): fix every unresolved item in its punch-list *first*, in priority order, then re-verify the full acceptance list below.

---

## 1. Yesterday's expected end state
None. **This is Day 1 — greenfield.** The repo currently contains only `docs/` (PRODUCT.md and these Build/Review folders). You are creating the entire project skeleton from scratch.

## 2. Today's goal (one sentence)
Stand up a **deployable, empty-but-real full-stack skeleton**: a Django+DRF backend running under ASGI with a health check, the Day-1 data models and migrations, Celery wired to Redis, an OpenAPI schema; a React+Vite PWA shell with routing, design tokens, and a generated API client; Docker for local infra; and CI green — plus a single proof that Claude can be called and return a **validated structured story "beat" JSON**.

## 3. Why this scope (context)
Day 1 buys us the rails everything else rides on: the folder layout, the module boundaries (Django apps = the services in §5.2), the streaming-capable ASGI server (§16.4), the token-driven design system (§15), and proof the AI layer can produce structured output (§8.1, §19.6). We deliberately build **no product features** today — no onboarding, no story engine, no auth flows. Just a spine that is correct and deployable.

---

## 4. Pre-flight (environment assumptions)
Assume the machine has: **Python 3.12+, Node 20+ (build tool only), Docker + Docker Compose, git**. If a tool is missing, stop and tell the founder exactly what to install — do not silently work around it. Create a `.env` from `.env.example` (see below). Secrets needed today: `ANTHROPIC_API_KEY` (for the hello-beat proof). If it is absent, still build everything else and make the hello-beat command fail loudly with a clear message rather than crashing the app.

---

## 5. Tasks by workstream

### 5.1 Repo & tooling (DevOps)
Create this exact top-level layout (establish it now; all later days depend on it):

```
backend/
  manage.py
  pyproject.toml              # or requirements.txt + requirements-dev.txt
  config/
    __init__.py
    settings/
      __init__.py
      base.py
      dev.py
      prod.py
    urls.py
    asgi.py                   # ASGI entrypoint (uvicorn)
    wsgi.py
    celery.py
  apps/
    common/                   # base models, shared utils
    accounts/                 # parent user + auth (models only today)
    profiles/                 # child profile (models only today)
    companion/                # companion (models only today)
    story/                    # session model + AI layer skeleton
    learning/  memory/  safety/  media/  analytics/  billing/  notifications/   # empty app shells today
  tests/
frontend/
  index.html
  package.json
  vite.config.ts
  tsconfig.json
  tailwind.config.js
  postcss.config.js
  public/manifest.webmanifest
  src/
    main.tsx
    App.tsx
    router.tsx
    routes/child/ChildHome.tsx
    routes/parent/ParentHome.tsx
    design/tokens.css
    design/tokens.ts
    lib/api/                  # generated client lands here
infra/
  docker-compose.yml          # postgres(+pgvector), redis, (optional) mailhog
  Dockerfile.backend
.github/workflows/ci.yml
.env.example
README.md
```

Tooling to configure:
- **Python:** `ruff` (lint+format), `black`-compatible via ruff, `mypy` (loose config OK today), `pytest` + `pytest-django`.
- **JS/TS:** `eslint`, `prettier`, `typescript`, `vitest` (a single smoke test).
- **pre-commit** config running ruff + prettier (optional but recommended).
- **Makefile** (or `justfile`) with: `make up` (docker infra), `make migrate`, `make run` (uvicorn ASGI), `make worker` (celery), `make front` (vite dev), `make test`, `make lint`, `make schema` (export OpenAPI), `make client` (generate TS client).

### 5.2 Backend — Django + DRF + ASGI (Backend)
- **Settings split** (`config/settings/base.py|dev.py|prod.py`), reading env via `django-environ` or `os.environ`. Installed apps include DRF, `drf_spectacular`, `django_celery_results`, `corsheaders`, `pgvector` (register the extension), and all `apps.*`.
- **ASGI:** `config/asgi.py` exposes `application`; run with `uvicorn config.asgi:application`. This is required for SSE streaming later (§16.4) — do not settle for WSGI-only.
- **Health check:** `GET /api/health/` → `{"status":"ok","service":"primer","version":"0.1.0"}` (200). No auth.
- **OpenAPI:** `drf-spectacular` serving `GET /api/schema/` (raw) and `GET /api/docs/` (Swagger UI). Even with few endpoints, the schema must generate cleanly.
- **CORS:** allow the Vite dev origin in dev.
- **Celery:** `config/celery.py` app, Redis broker + result backend, autodiscover tasks. Add a trivial task `apps/common/tasks.py::ping()` returning `"pong"`, callable to prove the worker runs.
- **Custom user:** `accounts.User` with **email as the login identifier** (email unique, `USERNAME_FIELD="email"`, no username). Set `AUTH_USER_MODEL="accounts.User"` **before the first migration** (critical — changing it later is painful).

### 5.3 Backend — Day-1 models & migrations (Backend)
Implement these models (subset of §17; only what Day 1 needs). Use a shared base.

`apps/common/models.py`:
```python
import uuid
from django.db import models

class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True
```

- `accounts.User` (custom, extends `AbstractBaseUser` + `PermissionsMixin`): `email`, `display_name`, `locale` (default "en-US"), `is_active`, `is_staff`, timestamps. Manager with `create_user`/`create_superuser`.
- `profiles.Child(UUIDModel)`: `parent` (FK→User, `related_name="children"`), `first_name`, `birthdate`, `locale`, `interests` (JSONField default list), `fears` (JSONField default list), `avoid_topics` (JSONField default list), `intensity_ceiling` (SmallInt, default 2), `session_cap_min` (SmallInt, default 15), `pre_reader` (bool default True).
- `companion.Companion(UUIDModel)`: `child` (OneToOne→Child, `related_name="companion"`), `archetype`, `name`, `palette` (JSON), `personality` (JSON), `voice_id` (nullable), `visual_descriptor` (Text), `evolution_state` (JSON default dict).
- `story.PlaySession(UUIDModel)`: `child` (FK→Child, `related_name="sessions"`), `started_at`, `ended_at` (nullable), `duration_s` (nullable int), `device` (char, nullable), `ended_reason` (char choices: completed/cap/exit, nullable).

Register **all four** in Django **admin** (`@admin.register`) with sensible `list_display` — the admin is our free ops/moderation console (§7.11). Generate and commit migrations. `python manage.py migrate` must succeed on a clean DB.

### 5.4 Backend — AI layer skeleton + hello-beat proof (AI)
Create `apps/story/ai/`:
- `schemas.py`: a Pydantic (or DRF serializer) model for a **Beat** matching the contract in PRODUCT.md §8.1 (fields: `beat_id`, `narration`, `scene{setting, illustration_prompt, characters_present}`, `interaction{type, prompt_audio?, expected?, choices?}`, `learning{objective_id, difficulty, skill_evidence_key}`, `emotion`, `continuity_refs`, `next_hint`).
- `router.py`: a minimal **model router** with a `generate_beat(context: dict) -> Beat` function. Provider order: Anthropic primary; leave typed stubs/hooks for OpenAI + Google fallback (not implemented today, just the seam per §19.3). Read `ANTHROPIC_API_KEY` from env; use the official `anthropic` Python SDK; request structured/JSON output; validate against `schemas.Beat`; on validation failure do ONE repair attempt then raise.
- `prompts/hello_beat.py`: a hardcoded minimal system+user prompt that asks for a single cozy, safe beat for "a 4-year-old who loves frogs" (no real child data). This is a *smoke test of the pipeline*, not the real story engine.
- Management command `apps/story/management/commands/hello_beat.py`: calls `generate_beat`, prints the validated Beat as pretty JSON, exits 0 on success, non-zero with a clear message on failure (including a friendly message if the API key is missing).

Keep this a **skeleton** — no memory, no safety layer, no images/TTS today. Those are later days.

### 5.5 Frontend — React + Vite PWA shell (Frontend + Design)
- **Vite + React + TypeScript** app. `App.tsx` renders a router (`react-router-dom`) with two trees: `/` → `routes/child/ChildHome` (placeholder "The Primer" cozy landing) and `/parent` → `routes/parent/ParentHome` (placeholder "Parent area"). No real functionality — just routed, styled placeholders proving the shell works.
- **Tailwind** wired to **design tokens**. Create `src/design/tokens.css` with CSS variables from PRODUCT.md §15 (the Storyland + Calm token sets: `--bg, --surface, --primary, --accent, --success, --text`, spacing scale `--space-1..10`, motion durations, type scale). Mirror them in `src/design/tokens.ts` (typed object) and reference the CSS vars from `tailwind.config.js` (`theme.extend.colors` etc.). The child route uses Storyland tokens; the parent route uses Calm tokens (a `data-theme` attribute on a wrapper is fine).
- **PWA:** `public/manifest.webmanifest` (name "The Primer", theme/background colors from tokens, icons placeholder, `display: standalone`) linked from `index.html`. A service worker is optional today (a stub is fine); do not over-invest.
- **API client:** after the backend schema exists, run `make schema` then `make client` to generate a typed client into `src/lib/api/` (use `openapi-typescript` + a thin fetch wrapper, or `orval`). Wire **TanStack Query** provider at the app root. Prove it by calling `GET /api/health/` from `ChildHome` and rendering the status somewhere unobtrusive (a tiny footer dot is fine).
- **TanStack Query + a Zustand store stub** installed and providers mounted (no real state yet).

### 5.6 CI & deploy (DevOps)
- `.github/workflows/ci.yml`: on push/PR → spin Postgres+Redis services, install backend deps, run `ruff`, `mypy` (non-blocking OK), `pytest` (must include the health-check test + a model smoke test); install frontend deps, run `eslint`, `tsc --noEmit`, `vitest`, `vite build`. CI must be **green**.
- **Deploy:** provide a working `infra/docker-compose.yml` (postgres with pgvector image, redis) and a `Dockerfile.backend`. Document staging deploy steps in `README.md` for a managed host (Render/Railway/Fly). Actually provisioning cloud staging may need founder credentials — if so, prepare everything and leave a clearly-marked `DEPLOY.md` checklist the founder completes. "Deployable" today means: `make up && make migrate && make run && make front` works locally end-to-end, and the container builds.

---

## 6. Key stubs (use these to remove ambiguity)

**`.env.example`**
```
DJANGO_SETTINGS_MODULE=config.settings.dev
SECRET_KEY=change-me
DEBUG=1
DATABASE_URL=postgres://primer:primer@localhost:5432/primer
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

**`infra/docker-compose.yml`** (shape)
```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment: { POSTGRES_USER: primer, POSTGRES_PASSWORD: primer, POSTGRES_DB: primer }
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
  redis:
    image: redis:7
    ports: ["6379:6379"]
volumes: { pgdata: {} }
```

**Health view** (DRF `APIView` or plain view) returning the JSON in §5.2. Add a pytest asserting 200 + shape.

---

## 7. Do NOT do today (scope guard)
- ❌ No onboarding, signup, or login *flows/endpoints* (User model only; auth endpoints are Day 2).
- ❌ No real story generation loop, no beats persisted, no choices (hello-beat is a throwaway proof).
- ❌ No memory, safety layer, images, or TTS.
- ❌ No parent dashboard content, no billing, no notifications.
- ❌ Don't add apps or models beyond those listed. Empty app shells for the rest are fine and expected.

## 8. Acceptance criteria (Day 1 is done when ALL are true)
Each is objectively checkable — the Review session will verify exactly these.

1. **Repo layout** matches §5.1 (backend/, frontend/, infra/, .github/, docs/ intact).
2. `docker compose -f infra/docker-compose.yml up -d` starts Postgres (pgvector) + Redis.
3. `python manage.py migrate` succeeds on a clean DB with **no pending migrations** afterward (`makemigrations --check` clean).
4. `AUTH_USER_MODEL` is `accounts.User` with email login; a superuser can be created and logs into `/admin/`.
5. All four models (`User`, `Child`, `Companion`, `PlaySession`) exist per §5.3, are registered in admin, and are creatable in admin without error.
6. Backend runs under **ASGI/uvicorn**; `GET /api/health/` returns 200 with the specified JSON.
7. `GET /api/schema/` and `/api/docs/` render without errors.
8. Celery worker starts and `ping()` task returns `"pong"` (a documented command proves it).
9. `python manage.py hello_beat` returns a **schema-valid Beat JSON** when `ANTHROPIC_API_KEY` is set, and fails with a clear message when it is not (no stack-trace crash).
10. Frontend `vite build` succeeds; `vite dev` serves `/` (child placeholder, Storyland tokens) and `/parent` (Calm tokens) via React Router.
11. Design tokens exist in both `tokens.css` and `tokens.ts` and are actually applied (visible theme difference between child and parent routes).
12. A typed API client is generated into `src/lib/api/` from the OpenAPI schema, and `ChildHome` successfully calls `/api/health/` via TanStack Query and shows the status.
13. PWA `manifest.webmanifest` is linked and valid (installable prompt criteria minimally met).
14. `make lint` and `make test` pass for both backend and frontend; **CI is green**.
15. `README.md` documents: prerequisites, `make` targets, how to run everything locally, and `DEPLOY.md` (or a README section) describes staging deploy.

## 9. Founder manual test (what Appu checks by hand at day end)
- Run `make up && make migrate && make run` — open `http://localhost:8000/api/docs/`, see the schema UI.
- Open `http://localhost:8000/admin/`, log in, create a Child + Companion by hand — no errors.
- Run `make front` — open the child page (should feel warm/Storyland) and `/parent` (should feel calm/neutral); confirm the health dot shows "ok".
- Run `python manage.py hello_beat` with your API key — see a valid cozy frog beat printed as JSON.
- Confirm CI is green on the pushed commit.

## 10. End state to hand to Day 2 (paste into Day 2's "Yesterday's expected end state")
A deployable full-stack skeleton exists: Django+DRF under ASGI with health check, OpenAPI schema, admin; models `User (email login)`, `Child`, `Companion`, `PlaySession` migrated; Celery+Redis working; AI router skeleton with a validated `Beat` schema and a passing `hello_beat` proof; React+Vite PWA shell with React Router (child/parent), design tokens (Storyland+Calm), TanStack Query, and a generated typed API client calling `/api/health/`; CI green; local run via `make` documented. **No auth flows, no story loop, no memory/safety/media yet.**
