# Build — Day 15: Eval Harness, Hardening & Alpha Launch (Milestone M2)

> **Session type:** BUILD. Read this whole file first. This is the **launch day** — do not ship anything unsafe. Build everything in scope, self‑review, then stop.
>
> **Source of truth:** `docs/PRODUCT.md` — §19.7 (eval pipeline), §16.10 (reliability/durability), §18.8 (rate limits/breaker), §6.1 (the MVP loop), §21.1 (cut‑lines), §22.5 (guardrail metrics).
>
> **If handed `docs/Review/reports/Day15-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Event pipeline with core metrics + per‑generation cost/session, product vs. parent analytics separated, accessibility pass complete (WCAG AA basics), polished onboarding/first‑run. Measurable, affordable, accessible, polished. Eval harness + launch hardening remain.

## 2. Today's goal (one sentence)
Ship a **safe, resilient production alpha**: a CI‑gating eval suite (golden + red‑team + age‑rubric + regression), backup/restore + feature flags + rate limits + an inference‑budget breaker, safe‑fallback on every error path, and 10–25 real families onboarded — reaching **Milestone M2 (the full §6.1 loop live for real families).**

## 3. Why this scope
Before real children arrive, evals must **gate every future change** (§19.7) and the system must degrade gracefully, never dangerously (§16.10). This day converts a working prototype into a launchable product with the safety and reliability a kids' product demands.

## 4. Tasks by workstream

### 4.1 AI / Eval — CI‑gating suite (§19.7)
Directory `evals/` with a runner wired into CI as a **required** check:
- **Golden scenarios:** coherence; the selected objective is actually exercised; character/companion consistency; warm close.
- **Red‑team:** injection via input/profile/memory, jailbreaks, fear‑ceiling probes, self‑harm/violence bait (extend the Day‑7 corpus).
- **Age‑appropriateness rubric:** model‑grader + a human spot‑check hook.
- **Regression:** cases distilled from every `docs/Review/reports/Day*-report.md`.
- **Gate:** a change that regresses safety/coherence beyond threshold **cannot deploy** (prove by forcing a fail → CI red → deploy blocked). Run an end‑to‑end red‑team pass against the live loop.

### 4.2 Backend — reliability & abuse protection
- **Backup/restore drill** (§16.10): Postgres PITR + a **tested restore** on a scratch DB (not just configured); media redundancy.
- **Feature flags** + **prompt/version management**: demonstrate a safe rollback of a prompt/model change.
- **Rate limits + global inference‑budget circuit breaker** (§18.8, §22.4): on trip, degrade to cached/fallback content behind narration — never an error to a child.

### 4.3 Frontend — final QA
- **Every** error path in the child UI degrades to a safe fallback (no raw errors ever reach a child). Full QA sweep across the whole §6.1 loop.

### 4.4 Launch
- Provision production; onboard **10–25 real families** (closed alpha) with a feedback channel. Confirm monitoring + the safety incident queue + guardrail metrics (§22.5: session‑length, sessions/day, incident rate) are live and watched.

## 5. Key stubs
- CI step: `pytest evals/ -q` as a **required** status check on the deploy workflow.
- Budget breaker: a per‑window inference‑spend cap that flips the app into fallback mode + alerts.

## 6. Do NOT do today
- ❌ No new product features — harden + launch only. ❌ Do **NOT** ship if the eval suite or any P0 safety case fails — take the §21.1 cut‑lines instead. ❌ Do **NOT** launch without a **tested** restore and a **live** incident queue.

## 7. Acceptance criteria (done when ALL true)
1. The eval suite (golden + red‑team + age‑rubric + regression) runs in CI and **blocks deploy** on threshold regression (prove: force a fail → deploy blocked).
2. End‑to‑end red‑team pass: no injection/jailbreak/ceiling breach succeeds against the **live** loop.
3. A backup is taken **and restored** successfully in a drill (data verified back), not merely configured.
4. Feature flags + prompt versioning allow a **demonstrated** rollback.
5. Rate limits + the inference‑budget breaker work; on trip, the child sees warm fallback content, not an error.
6. **Every** error path in the child UI degrades to a safe fallback (no raw errors reach a child).
7. Production alpha is live; **10–25 families onboarded**; monitoring + safety incident queue + guardrail metrics active and watched.
8. Full regression + safety pass; `make lint && make test` + **CI green**. **M2 reached.**

## 8. Founder manual test
Force an eval failure and confirm CI **blocks** the deploy. Trip the budget breaker and confirm the child still gets warm content. Do a restore drill and confirm data comes back. Then walk the whole **§6.1 loop** in production as a real family: onboard → child plays a personalized, illustrated, voiced, remembered, safe adventure → return next day and be remembered → read the parent report. That is the thesis, live.

## 9. End state (MVP complete)
A safe, resilient production alpha of the full §6.1 loop is live for real families: personalized illustrated voiced branching adventures with a consistent companion that remembers day‑to‑day, invisible learning, defense‑in‑depth safety, parental controls + transparency, metrics + cost visibility — all behind an eval suite that **gates every future change**, with tested backups, feature flags, and graceful degradation. **Milestone M2 complete. The MVP thesis is now testable with real children and parents.** Next: the Month‑2 roadmap (PRODUCT.md §20.4) — voice input, world depth, more learning domains, and iterating on real retention/learning signal.
