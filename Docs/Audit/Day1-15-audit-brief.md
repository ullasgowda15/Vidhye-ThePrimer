# Full-Product Security & Quality Audit — Day 1 → Day 15

> **Hand this file to a fresh Claude Code session (in `cmd`, at the repo root).** It is a standalone brief; it assumes no memory of the sprint.
>
> **Why this exists.** The 15-day sprint was reviewed one day at a time, and each review passed. But a per-day review is structurally blind to three things: **cross-day integration bugs** (a Day-9 change that quietly broke a Day-4 assumption), **cumulative drift** (something that was fine on Day 6 and is wrong by Day 14), and **whole-product security holes** that only appear when you attack the finished system rather than one day's diff. The Day-15 review already found one such thing the per-day reviews missed — an injection that reached the safety-policy *instruction* layer through a parent's profile field, unseen because the injection eval only drove one of eight input surfaces. **Assume there are siblings of that bug. Your job is to find them.**
>
> **This is a real product about to be shown to real families with real children.** Hold the bar at "proven", not "plausible". Low exploitability is not a pass — this codebase's own thresholds say "§13 admits no rate."

---

## Ground rules

1. **You are an independent auditor, not the builder.** Do **not** trust the reports in `docs/Review/reports/` or `docs/PROGRESS.md` — read them to learn what was *claimed*, then verify independently. A green test is a claim; re-run it and probe around it.
2. **Read-only on product code during the audit.** Write your findings to `docs/Audit/Day1-15-audit-report.md`. Do not fix as you go — a fix mid-audit changes the thing you're auditing. (If the founder later asks for a fix pass, that's a separate phase.)
3. **Run real commands.** `make lint`, `make test`, `make evals`, `pip-audit`, `npm audit`, and your own probe scripts against a real local stack (`make up`). Write probes to a scratch test DB; delete them after.
4. **Prove every finding.** Each one needs: severity, the exact file/line, a reproduction (command or probe), and the concrete impact on a child or a family. A finding without a repro is a hypothesis — mark it as one.
5. **Distinguish new findings from known-open ones.** `docs/PROGRESS.md` lists carried-forward P2s (e.g. `chosen_key` in the warehouse export, the weekly-report sitting count, the lexical flag-name guard). Note if you confirm one, but the value here is what *nobody has written down yet*.

## Severity rubric

- **P0 (blocker):** ships harm or a privacy/security breach. Any of the "automatic P0" list below.
- **P1 (must fix before families):** a real defect a real user hits, or a guarantee that's weaker than claimed.
- **P2 (should fix):** correctness/robustness gaps, latent bugs, contract mismatches.

**Automatic P0 — treat any of these as stop-the-line:**
- Any injected instruction reaching an **instruction layer** of a prompt, or any unsafe content reaching a **child**, through *any* input surface.
- Any **cross-account** read or write — one family reaching another family's child, stories, memories, settings, reports, or export.
- **Deletion** leaving any child data behind — Postgres, memory vectors, R2/object storage, analytics events, cost spans, Redis, logs, or backups.
- A **safety-layer bypass** — a beat reaching a child without passing the independent grader; the fallback failing; the ceiling not enforced on some generation path.
- A **secret** committed to the repo, or child data in logs/telemetry.
- The **ship gate** not actually blocking a bad deploy.

---

## Scope — audit each area, with concrete attacks to try

### 1. Child safety, end-to-end (§13)
- **Injection through every input surface, not one.** Enumerate every place parent- or child-supplied text enters a prompt: `child.first_name`, `interests`, `avoid_topics`, `fears`, companion name/descriptor, the child's typed answers on `choose` beats, memory content, plan fields. For each, plant an injection and trace where the residue lands — **DATA layer (acceptable) vs INSTRUCTION layer (P0)**. The Day-15 fix covered the profile fields via `assembler._instruction_text`; confirm it holds for *all* of them and that no new surface was added later that skips it.
- **Ceiling enforcement on every generation path** — story beats, the greeting, personalized planning, the cap-close beat, any fallback. Set the gentlest ceiling and try to get frightening content through each path.
- **Safety grader independence (§13.3)** — confirm the safety model can never be the beat model, including via the `ai.beat_model` flag and the router's model-pinning. Confirm `SAFETY_CLASSIFIER_ENABLED` can't be off in prod.
- **The fallback never fails and never leaks** — force max rewrites; force the grader to reject repeatedly; confirm the pre-vetted fallback beat is served and is itself safe.
- **No raw error / stack / score / streak / FOMO reachable by a child** on any path (render error, network error, quota 429, missing audio).

### 2. Cross-account / multi-tenant isolation
- **IDOR sweep.** With two real families and real tokens, hit **every** endpoint with the *other* family's `child_id`, `adventure_id`, `session_id`, transcript id, export, delete. Expect 401/403/404 everywhere; a 200 is a P0.
- **Every list endpoint** filters by the requesting child/parent (no unscoped querysets that leak in aggregate).
- **The export** contains only the requesting family's data (plant a distinctive string in family B, assert it never appears in family A's bundle).
- **Admin surfaces** don't print a child's name where a broader-access staff account shouldn't see it.

### 3. Data deletion & privacy (COPPA/GDPR)
- **Delete a child and an account, then hunt for survivors** in *every* store: Postgres (FK walk), `Memory.embedding` vectors, R2/object storage (the Day-13 P0 was orphaned media — confirm the fix and look for other object types), `Event` + `GenerationCost` analytics (the Day-14 P0 was surviving cost spans — confirm), Redis STM, and any log line carrying identifying data. Backups are out of scope to delete but the disclosure's claims about them must be true.
- **Export completeness & honesty** vs what `Privacy.tsx` / `accounts/privacy.py` promise. Any claim in the disclosure that isn't true in code is at least P1 (this bit twice already).
- **Consent gating** — confirm consent gates *processing* and never a parent's *access* to their own data, and that a second family still gets 403 regardless.

### 4. Auth & session security
- JWT/refresh-cookie handling; the `SameSite=Lax` refresh endpoint is the load-bearing CSRF control (`accounts/cookies.py`) — confirm it can't be exercised cross-site, and that the API/PWA same-site deployment constraint is documented and enforced.
- Account deletion requires the password (not just a bearer token).
- Throttles present and effective on auth, refresh, greeting, companion creation.
- Token expiry/rotation; no long-lived tokens; logout invalidates.
- `prod.py` boot guards actually refuse: safety off, image moderation off, media=local, missing budget, missing encryption key, missing SECRET_KEY.

### 5. Prompt / LLM integrity
- Injection **through memory** and **through personalization/greeting** (not just profile fields) — the labeled-DATA fence must hold on every path that assembles a prompt.
- Structured-output failures (refusal, truncation, parse error) degrade safely and are cost-metered (Day-14 P1-5).
- The **budget breaker fails open** — a trip never refuses a cached asset or the safety substitution; a dead Redis means "not enforcing", surfaced, not silently uncapped.

### 6. Cost & analytics integrity + privacy
- **No child content in telemetry** — read every `events.track` call site; `props` must be shape, never content. The child's typed answer (`Choice.value`) and narration must be on no event. Check `chosen_key` (a known P2) and any other client-supplied field reaching the warehouse export that no delete can reach.
- Analytics honors deletion (`Event`/`GenerationCost` cascade).
- Cost attribution correct across Celery boundaries; no PII in logs.

### 7. Data integrity & concurrency
- **Idempotency under Celery redelivery/races** for all post-session tasks: memory consolidation, mastery, interest graph, session report — and their ordering gates. Fire duplicates and out-of-order; assert no double-counting and no lost evidence.
- **Migrations apply cleanly from a fresh database** (`make up` on an empty volume → `make migrate`), all 15 days' migrations in order, no manual steps.
- Look for the **flaky-test class** the Day-14 review found (a fractional-day time seed that fails ~5h/24) — run the suite at an unusual UTC hour, or grep for other time-relative seeds/`timezone.now()` in tests that could hide real failures.

### 8. Frontend / child UX safety
- No spinner/error/score/streak/FOMO reachable on the child surface (grep + actually drive the error boundaries).
- Error boundaries: child gets a resting companion; parent gets an actionable sentence.
- Accessibility toggles genuinely function (not just render) — captions, reduced motion, dyslexia font, high contrast, volume, voice-off.
- **No `localStorage`/`sessionStorage`** holding sensitive data; check what the PWA caches offline.

### 9. Config & deploy safety
- Secrets: `.env` gitignored, none committed anywhere in history (`git log -p` grep for keys); `.env.example` carries no real values.
- `DEBUG=0`, `ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS` correct for prod, HSTS/secure cookies on.
- The ship gate: `deploy.yml` `release` genuinely `needs: [gate]`, no `continue-on-error`, and a forced-regression turns it red (drill it).

### 10. Dependencies & supply chain
- `pip-audit` (or `safety`) on backend deps; `npm audit` on frontend. Report known-vuln CVEs with severity. Confirm deps are pinned.

### 11. Cross-cutting regression
- Fresh clone → `make install` → `make up` → `make migrate` → `make lint` → `make test` → `make evals`. Everything green from cold, numbers matching what PROGRESS.md claims. Any discrepancy is a finding.

---

## Deliverable

Write `docs/Audit/Day1-15-audit-report.md` with:

1. **Verdict** — SHIP / FIX-THEN-SHIP / NO-GO for showing families, with the one-line reason.
2. **Findings table** — id, severity, area, file:line, one-line impact.
3. **Per-finding detail** — the proof (command/probe + output), the impact on a child or family, and a suggested fix *direction* (not the fix itself).
4. **What you verified and found clean** — so the founder knows the coverage, not just the holes.
5. **What you could not verify** and why (e.g. needs a real provider key, needs production).
6. **Known-open items you confirmed** (the carried P2s), separated from new findings.

End with the honest bottom line: **is this safe to put in front of a child, yes or no, and what must change first.**
