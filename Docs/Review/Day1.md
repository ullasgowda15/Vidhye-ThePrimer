# Review — Day 1: Project Foundation

> **Session type:** REVIEW. You are Claude Code acting as an **independent, skeptical auditor**. You did **not** build this. Your job: verify Day 1's build against its acceptance criteria by gathering real evidence (run commands, open files, hit endpoints), then write a report to `docs/Review/reports/Day1-report.md`.
>
> **Hard rule: DO NOT modify application code, migrations, config, or fix anything.** You may create/overwrite only your report file. If something is broken, you *report* it — a Build session fixes it.
>
> **Inputs:** the current repo state + `docs/Build/Day1.md` (the spec you are auditing against) + `docs/PRODUCT.md` §15/§16/§17 for architecture correctness.

---

## 1. How to review (method)
Be evidence-driven. For each criterion below: state what you **did** (command run / file opened), the **observed result** (paste the key output or file excerpt), and a **verdict** (✅ Done / 🟡 Partial / ❌ Not built / 🔵 Can be improved). Never mark ✅ without evidence. If you cannot run something (e.g., no API key, no Docker), say so explicitly and mark it **⚪ Unverified** with the reason — do not guess.

Suggested commands (run what the environment allows):
```
# structure
ls -R backend frontend infra .github | head -100
# infra
docker compose -f infra/docker-compose.yml config
# backend
cd backend && python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py check
pytest -q
uvicorn config.asgi:application &  then  curl -s localhost:8000/api/health/
curl -s localhost:8000/api/schema/ | head
# celery
celery -A config worker -l info &  then trigger ping()
# ai proof (only if ANTHROPIC_API_KEY set)
python manage.py hello_beat
# frontend
cd ../frontend && npm ci && npm run lint && npx tsc --noEmit && npm run build && npm run test
```

## 2. Checklist to verify (maps 1:1 to Day 1 acceptance criteria)
Audit each; record verdict + evidence.

1. **Repo layout** matches Build §5.1 (backend/, frontend/, infra/, .github/, docs/ intact; apps/ contains all listed Django apps as shells).
2. **Infra up:** `docker-compose.yml` is valid and starts Postgres (pgvector image) + Redis.
3. **Migrations:** `migrate` succeeds clean; `makemigrations --check` reports **no** missing migrations.
4. **Custom user:** `AUTH_USER_MODEL = accounts.User`, email is the login field, superuser can log into `/admin/`.
5. **Models:** `User`, `Child`, `Companion`, `PlaySession` exist with the fields in Build §5.3, are admin-registered, and creatable via admin. (Check field names/types against PRODUCT.md §17.)
6. **ASGI + health:** app runs under uvicorn; `GET /api/health/` → 200 with `{"status":"ok","service":"primer","version":...}`.
7. **OpenAPI:** `/api/schema/` and `/api/docs/` render without error.
8. **Celery:** worker starts against Redis; `ping()` returns `"pong"`.
9. **hello-beat:** with a key, returns **schema-valid** Beat JSON (validate the shape against PRODUCT.md §8.1); without a key, fails with a clear message, no raw crash. (If no key available, mark ⚪ Unverified but still inspect `schemas.py`/`router.py`/`prompts/` for correctness.)
10. **Frontend build+routes:** `vite build` succeeds; `/` (child, Storyland) and `/parent` (Calm) render via React Router.
11. **Design tokens:** present in BOTH `tokens.css` and `tokens.ts`, applied, with a visible theme difference between child and parent. Cross-check token names against PRODUCT.md §15.
12. **API client:** typed client generated into `src/lib/api/` from the schema; `ChildHome` calls `/api/health/` via TanStack Query and displays status.
13. **PWA manifest:** linked from `index.html`, valid fields (name, colors, display standalone).
14. **Lint/test/CI:** `make lint` + `make test` pass both stacks; `.github/workflows/ci.yml` exists and is green (check the workflow logic even if you can't see the remote run).
15. **Docs:** `README.md` covers prerequisites, `make` targets, local run, and staging deploy steps (or `DEPLOY.md`).

## 3. Cross-cutting quality checks (beyond the checklist)
- **Architecture fidelity:** do the Django apps match the module boundaries in PRODUCT.md §5.2? Is ASGI (not just WSGI) genuinely the run path (SSE depends on it, §16.4)?
- **Scope discipline:** did the build stay within Day 1? Flag any front-running (auth flows, story loop, memory/safety/media, extra models) as 🔵/🟡 — it's a risk even if it "works."
- **Migration hygiene:** `AUTH_USER_MODEL` set before first migration? Single clean initial migration per app, not a pile of edits?
- **Secret hygiene:** no secrets committed; `.env` gitignored; `.env.example` present.
- **Reproducibility:** could a fresh clone reach a running app using only the README + `make`? Note any undocumented manual step.

## 4. Report to write → `docs/Review/reports/Day1-report.md`
Create the file using **exactly** this structure:

```markdown
# Day 1 Review Report — Project Foundation
**Reviewed:** <UTC timestamp>  ·  **Commit:** <git rev-parse --short HEAD>  ·  **Reviewer:** Claude Code (review session)

## Overall verdict
<SHIP | FIX-THEN-SHIP | NOT-READY> — <one-sentence justification>

## Scorecard
| # | Criterion | Verdict | Evidence (cmd/file → result) |
|---|-----------|---------|------------------------------|
| 1 | Repo layout | ✅/🟡/❌/⚪ | ... |
| ... | ... | ... | ... |
| 15 | Docs | ... | ... |

Counts: ✅ _n_ · 🟡 _n_ · ❌ _n_ · 🔵 _n_ · ⚪ _n_

## What's working well
- ...

## Punch-list (hand this to the next Build session)
### P0 — blockers (must fix before Day 1 can close)
- [ ] ...
### P1 — should fix today
- [ ] ...
### P2 — improvements (non-blocking, log for later)
- [ ] ...

## Scope & risk notes
- Front-running / architecture deviations / migration or secret concerns: ...

## Unverified items (and why)
- ... (e.g., "hello_beat — no ANTHROPIC_API_KEY in env")
```

## 5. Verdict rules (be strict and consistent)
- **NOT-READY** if any P0 exists (e.g., migrations fail, app won't run under ASGI, health check missing, models wrong, CI red).
- **FIX-THEN-SHIP** if no P0 but ≥1 P1.
- **SHIP** only if every acceptance criterion is ✅ (⚪ Unverified allowed *only* for genuine environment limits, and each must be listed).
- Prefer honesty over optimism. A generous review that lets a broken foundation through costs the founder far more on Day 5. If it's not proven, it's not done.

## 6. After writing the report
Print a 5-line summary to the terminal: overall verdict, the counts, and the P0/P1 item titles. Do nothing else — do not fix, do not proceed to Day 2.
