# Build Folder — How this works

This folder contains **15 build-day briefs** (`Day1.md` … `Day15.md`). Each brief is a **self-contained prompt for a fresh Claude Code session** that builds that day's slice of The Primer. It assumes no memory of prior sessions — everything a session needs to start is written into the file.

## Briefs that are not build-days

**`Reading-Integration.md`** — a *programme* brief rather than a day: five phases
that fold the Vidhye oral-reading engine into The Primer, written after the
15-day sprint. It works the same way (scope, acceptance criteria, "Do NOT do",
end state) but each phase is its own session, and a session should be handed the
programme brief *plus* the phase number. Phase 0 is complete.

## The daily loop (founder = Appu, builder + reviewer = Claude Code)

1. Open Claude Code in the project root. Paste/hand it **`docs/Build/DayN.md`**.
2. Claude builds the day's slice (backend, frontend, design, AI — all of it) and self-reviews as instructed.
3. Open a **new** Claude Code session. Hand it **`docs/Review/DayN.md`**.
4. Claude reviews against the acceptance criteria and writes **`docs/Review/reports/DayN-report.md`** (verdicts + prioritized punch-list). It does **not** modify code.
5. If the report has unresolved items: open a **new** build session, hand it **`docs/Build/DayN.md` again plus `docs/Review/reports/DayN-report.md`**. Claude fixes the punch-list first.
6. Repeat 3–5 until you (Appu) are satisfied. Then run the **Founder manual test** at the bottom of the Build brief. Day done.

## Conventions used in every Build brief

- **Project root** is the repo folder Claude Code is opened in. All paths are relative to it.
- **Canonical layout** (established Day 1, stable thereafter):
  ```
  backend/    # Django project (config/ + apps/*)
  frontend/   # React + Vite SPA/PWA
  infra/      # docker-compose, Dockerfiles
  .github/    # CI
  docs/       # this documentation (PRODUCT.md is the source of truth)
  ```
- **Source of truth for product/tech decisions:** `docs/PRODUCT.md`. Build briefs reference section numbers instead of repeating rationale. If a brief and PRODUCT.md ever conflict, the brief wins for *scope-of-the-day*, PRODUCT.md wins for *architecture*.
- **Scope discipline:** each brief has an explicit **Do NOT do today** list. Respect it — future days depend on not front-running.
- **Every build-day ends deployable to staging**, lint/format/typecheck green, migrations applied, and with the day's acceptance criteria satisfiable.
- **Definition of Done handoff:** the last section of each brief, "End state to hand to Day N+1", is copied into the top of the next day's brief as "Yesterday's expected end state" so continuity is explicit.

## Stack (see PRODUCT.md §16)
Python 3.12 · Django + DRF (ASGI via uvicorn) · Celery + Redis · PostgreSQL + pgvector · React + Vite + Tailwind (PWA) · a provider-agnostic model router, **pointed at Gemini on Vertex AI as deployed** (beats, safety, memory, image moderation and embeddings on one service account; Anthropic and OpenRouter still selectable, OpenAI a seam, no OpenAI account required) · Stripe · S3-compatible storage + CDN.
