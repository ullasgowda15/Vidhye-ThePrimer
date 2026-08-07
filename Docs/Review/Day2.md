# Review — Day 2: Auth, Parent Account & Child Profile

> **Session type:** REVIEW. You are an **independent, skeptical auditor** — you did not build this. Gather real evidence (run commands, hit endpoints, read code), then write `docs/Review/reports/Day2-report.md`. **Hard rule: DO NOT modify application code, migrations, or config — only your report file.** Audit against `docs/Build/Day2.md` + PRODUCT.md §13.7/§17.2/§18.3. Verdicts: ✅ Done / 🟡 Partial / ❌ Not built / 🔵 Can improve / ⚪ Unverified — each with evidence.

## 1. Method
Exercise the API with real calls; never mark ✅ without evidence. Prove auth, ownership, cascade, consent‑gate, and the parent gate by actually triggering them.

```bash
# auth round-trip
curl -sX POST :8000/api/v1/auth/signup -H 'Content-Type: application/json' \
     -d '{"email":"a@b.com","password":"Str0ng!pass","display_name":"A"}'
ACCESS=$(curl -sX POST :8000/api/v1/auth/login -d '{"email":"a@b.com","password":"Str0ng!pass"}' | jq -r .access)
curl -s :8000/api/v1/me -H "Authorization: Bearer $ACCESS"
# refresh rotation: use refresh, then try the OLD refresh again -> must be rejected (blacklisted)
# consent gate: POST /children BEFORE any consent -> expect 4xx
# child CRUD + cascade: create child, note id, DELETE, then in psql confirm no rows reference it
# ownership: parent B's token requesting parent A's child -> 403
# throttling: loop an endpoint past the limit -> 429
cd backend && pytest apps/accounts apps/profiles apps/safety/prompting -q
```
Also open `/admin/` and confirm `Child` + `ConsentRecord` rows; inspect the DB for orphans after delete (`psql \d+` the child‑referencing tables).

## 2. Checklist (1:1 with Day 2 acceptance criteria)
1. **Auth:** signup/login/refresh/logout/me behave; refresh **rotates + blacklists** the old token; logout invalidates.
2. **Consent gate:** child creation is impossible without an active consent record — enforced **server‑side** (evidence: the pre‑consent request 4xx'd).
3. **Child CRUD + validation:** ceilings 0–3, caps 5–30, ages, seed lists coerced; PATCH works; seeds persist.
4. **Cascade:** DELETE leaves **zero** referencing rows (paste the psql check).
5. **Ownership + throttling:** cross‑parent → 403; over‑limit → 429.
6. **Onboarding + gate:** UI flow completes; parent gate blocks entry; `ProtectedRoute` redirects unauthenticated users.
7. **Prompt assembler:** deterministic per fixed child; ceiling + every avoid‑topic present in the safety layer; age‑band changes phrasing (read the tests + run them).
8. **Schema/client/CI:** OpenAPI regenerates; TS client updated + used; lint/test/CI green.

## 3. Cross-cutting checks
- **Security:** passwords hashed (not reversible); tokens never logged; refresh rotation truly revokes; CORS scoped to the dev origin (not `*`); no secrets committed; `.env` gitignored.
- **COPPA fidelity (§13.7):** consent is verifiable‑*lite* but real (method + timestamp + evidence recorded); disclosure text is plain‑language.
- **Schema fidelity:** `Child`/`ConsentRecord` fields match PRODUCT.md §17.
- **Scope discipline:** no companion/story/billing front‑run; the prompt assembler is NOT wired into any live generation.

## 4. Report to write → `docs/Review/reports/Day2-report.md`
Use the **same structure as `reports/Day1-report.md`** (title: "Day 2 Review Report — Auth, Parent Account & Child Profile"): header (timestamp + `git rev-parse --short HEAD` + reviewer), Overall verdict, Scorecard table (criteria 1–8 + verdict + evidence, plus counts), What's working well, Punch‑list (P0 blockers / P1 / P2), Scope & risk notes, Unverified items (with reasons).

## 5. Verdict rules
**NOT‑READY** if any P0: auth broken, consent bypassable, cascade leaves orphans, ownership leaks another family's data, CI red. Security/consent failures are **always P0**. **FIX‑THEN‑SHIP** if no P0 but ≥1 P1. **SHIP** only if all 8 are ✅ (⚪ allowed only for genuine env limits, each listed).

## 6. After writing the report
Print a 5‑line terminal summary (overall verdict, the counts, P0/P1 titles). Do nothing else — do not fix, do not proceed to Day 3.
