# CLAUDE.md — The Primer (project context for Claude Code)

You are building **The Primer**, an AI-generated living storybook that is a personalized lifelong learning companion for children aged 3–7. This file is auto-loaded every session. Read it, then do what the current session's brief tells you.

## Source of truth
- **`docs/PRODUCT.md`** is the authoritative product + technical spec (architecture, schema, API, safety, AI design). **Do not read it end-to-end every session** — it's long. Each daily brief references the specific sections you need (e.g. "§17.2"); open only those.
- If a daily brief and PRODUCT.md ever conflict: the **brief wins for scope-of-the-day**, PRODUCT.md wins for **architecture**.

## How the build works (15-day sprint, solo founder + you)
Work happens one day at a time in two alternating session types:

- **BUILD session** — you are handed `docs/Build/DayN.md`. Build everything in its scope, follow its acceptance criteria, respect its **"Do NOT do today"** list, self-review, then **stop** (do not start the next day). If you are *also* handed a review report (`docs/Review/reports/DayN-report.md`), fix its punch-list first (P0→P1), then re-verify.
- **REVIEW session** — you are handed `docs/Review/DayN.md`. Act as an independent auditor: verify the build against the acceptance criteria by running real commands, then write `docs/Review/reports/DayN-report.md`. **Do NOT modify code in a review session** — only write the report.

`docs/Build/README.md` and `docs/Review/README.md` describe this loop in full. Read the relevant one if unsure.

## Stack (per PRODUCT.md §16) — do not swap without being told
Python 3.12 · Django + Django REST Framework (run under **ASGI/uvicorn** — required for SSE) · Celery + Redis · PostgreSQL + pgvector · React + Vite + Tailwind (PWA; Node is a build tool only, no Node server) · a provider-agnostic model router · Stripe · S3-compatible storage + CDN.

**What the router is actually pointed at, as deployed:** **Gemini on Vertex AI**, one service account, for every route that carries child data — beats (`gemini-2.5-flash`), the safety grader and memory digest (`gemini-2.5-flash-lite`), image moderation, and embeddings (`gemini-embedding-001` at 1536 dims). Anthropic Claude and OpenRouter are still implemented providers and stay selectable by configuration; OpenAI is a declared seam. **There is no OpenAI account in this product's supply chain** — if you are about to write `OPENAI_API_KEY` into a required-config list, that is now wrong. See `Docs/Hosting.md §3/§4` and `.env.example`.

## Standing rules (every session)
- **Scope discipline:** build only the current day's scope. Do not front-run future days — later days depend on it.
- **End every build-day deployable:** migrations applied, `make lint` + `make test` green, CI green.
- **Safety is non-negotiable:** the child never sees a raw error or a spinner; generation is wrapped by the safety layer (from Day 7 on). Never weaken this to move faster.
- **Ask before large deviations:** if a brief seems wrong or blocked, say so and propose a fix — don't silently improvise a different architecture.

## Git discipline
Commit at the end of each build-day (and after fixing a review punch-list) with a clear message, e.g. `Day 3: companion identity + media pipeline`. This lets review sessions diff and lets you roll back a bad day.

## Canonical layout (established Day 1)
```
backend/    # Django project: config/ (settings, asgi, celery) + apps/*
frontend/   # React + Vite SPA/PWA
infra/      # docker-compose, Dockerfiles
docs/       # PRODUCT.md (source of truth) + Build/ + Review/
```
