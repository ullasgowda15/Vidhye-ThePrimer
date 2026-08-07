# Build — Day 13: Settings, Controls & Wellbeing

> **Session type:** BUILD. Read this whole file first. Build everything in scope, self‑review, then stop — do **not** start Day 14.
>
> **Source of truth:** `docs/PRODUCT.md` — §12.7 (settings), §12.8 (privacy center), §13.4 (age/ceiling enforcement), §13.6 (anti‑over‑engagement — hard ethical lines), §13.7–13.8 (consent/privacy), §7.13 (settings list).
>
> **If handed `docs/Review/reports/Day13-report.md`:** fix its punch‑list first (P0→P1), then re‑verify.

---

## 1. Yesterday's expected end state
Parents get honest session + weekly reports, milestone‑mapped insights, and a verbatim transparency log — owner‑scoped, grounded in real data. No settings/controls or privacy center yet.

## 2. Today's goal (one sentence)
Put the parent **in control and protect the child's wellbeing**: enforceable settings (session cap, time window, intensity ceiling, avoid‑topics, voice/text/accessibility), a warm companion‑delivered "time to go" ending, and a privacy center (export + full‑cascade delete).

## 3. Why this scope
Parental control + wellbeing guardrails are trust table‑stakes and an ethical non‑negotiable (§13.6). Settings must be **honored immediately** in **both** generation and the safety guard (§13.4). The anti‑over‑engagement rules protect the child and the brand — and are a hard line, not a preference.

## 4. Tasks by workstream

### 4.1 Backend — settings + enforcement
```
GET   /api/v1/children/{id}/settings  -> Settings
PATCH /api/v1/children/{id}/settings  {session_cap_min, time_window, intensity_ceiling,
                                       avoid_topics[], voice_on, text_on, accessibility{...}}
```
- Settings live on `Child` (extend as needed). **Read them into BOTH** `apps/safety/prompting/assembler.py` (generation) **and** `apps/safety/pipeline.py` (guard) so avoid‑topics never appear and intensity never exceeds the ceiling (§13.4).
- **Wellbeing enforcement** (§13.6): when a session hits `session_cap_min`, the orchestrator ends the current adventure at a **warm close** ("time to go", companion‑delivered, positive — never a hard cutoff). Respect the daily `time_window`.

### 4.2 Backend — privacy center (§12.8)
```
POST   /api/v1/account/export   -> a portable archive of parent-owned data (profiles, memories, adventures, reports)
DELETE /api/v1/children/{id}    -> full cascade (re-verify Day 2 behavior)
DELETE /api/v1/account          -> full account + all children cascade
```
- Plain‑language privacy disclosure endpoint/content (accurate: no sale, no third‑party training, §13.8).

### 4.3 Frontend
- `src/routes/parent/Settings.tsx` (simple, prominent, immediately effective) + `Privacy.tsx` (export/delete with confirmation).
- Wire **accessibility toggles** into the child experience: captions, reduced‑motion, dyslexia‑friendly font, high‑contrast, adjustable audio (§14.6).

### 4.4 Child experience
- The companion delivers the "time to go" ending warmly and positively (§13.6).

## 5. Key stubs
- `settings_for(child)` used by both assembler and guard.
- Export format: a zipped JSON bundle (parent‑owned data only), streamed as a download.

## 6. Do NOT do today
- ❌ No billing. ❌ **Do NOT add any child‑directed re‑engagement / notifications / streak pressure / FOMO** — hard ethical line (§13.6). ❌ Do NOT let a setting be ignored by generation or safety. ❌ No new story features.

## 7. Acceptance criteria (done when ALL true)
1. Each setting is honored immediately in **both** generation **and** the safety guard (an avoid‑topic never appears; intensity never exceeds the ceiling — prove by trying).
2. Session cap ends the current adventure at a **warm** close; the daily time window is enforced.
3. There are **zero** child‑directed notifications/streaks/FOMO mechanics anywhere (prove by code inspection).
4. Accessibility toggles (captions, reduced‑motion, dyslexia font, high‑contrast, adjustable audio) work in the child UI.
5. `export` returns portable parent‑owned data; `delete` (account + child) cascades **completely** (no orphans).
6. The privacy disclosure is plain‑language and accurate.
7. `make lint && make test` + **CI green**, incl. a test asserting an avoid‑topic never appears and the cap ends warmly.

## 8. Founder manual test
Set an avoid‑topic + a low intensity ceiling + a short session cap → play, and confirm the topic never shows, intensity stays gentle, and the session ends warmly at the cap. Toggle each accessibility option and see it take effect. Export your data; delete a child and confirm it's fully gone.

## 9. End state to hand to Day 14
Enforceable parental controls (honored in generation **and** safety), warm session‑cap/time‑window wellbeing behavior with **zero** child‑directed pull mechanics, working accessibility toggles, and a privacy center (export + full‑cascade delete). Parents are in control; the child is protected. No billing; no analytics/eval hardening yet.
