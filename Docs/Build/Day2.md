# Build — Day 2: Auth, Parent Account & Child Profile

> **Session type:** BUILD. You are Claude Code, the solo builder's whole team. Read this entire file before writing code. Build everything in scope, self-review against the acceptance criteria, then stop — do **not** start Day 3.
>
> **Source of truth:** `docs/PRODUCT.md` — §4.2 (onboarding), §13.7 (verifiable parental consent), §17.2 (schema), §18.2–18.3 (API conventions/endpoints), §14.2 (parent gate), §19.1–19.2 (prompt layers).
>
> **If you were also handed `docs/Review/reports/Day2-report.md`:** fix every unresolved item in its punch-list first (P0→P1), then re-verify the full acceptance list below.

---

## 1. Yesterday's expected end state
A deployable full-stack skeleton exists: Django+DRF under ASGI with health check, OpenAPI schema, admin; models `User (email login)`, `Child`, `Companion`, `PlaySession` migrated; Celery+Redis working; AI router skeleton with a validated `Beat` schema and a passing `hello_beat`; React+Vite PWA shell with React Router (child/parent), design tokens, TanStack Query, a generated typed API client; CI green. **No auth flows, no story loop, no memory/safety/media.**

## 2. Today's goal (one sentence)
Ship the complete **parent onboarding path**: JWT signup/login/refresh/logout, a parent gate, COPPA‑lite verifiable‑consent capture, child‑profile CRUD carrying the personalization seeds (loves/fears/avoids/ceilings), owner‑scoped permissions + throttling, and the tested **prompt‑assembly module** that turns a child's age + ceilings + avoid‑list into system/safety prompt layers.

## 3. Why this scope
Auth is the gate to consent, billing, and data ownership (§7.1). The child seeds are the cold‑start for every downstream personalization decision (§3.5). Consent is the legal floor and the first trust moment (§13.7). The prompt‑assembly module (§19.1–19.2) is built now — decoupled from any live generation — so Days 4–5 can simply call it. We build it test‑first because it is a safety‑relevant component.

## 4. Tasks by workstream

### 4.1 Backend — `accounts` app (auth + consent)
Endpoints (all under `/api/v1/`, per §18.2):

```
POST /auth/signup     {email, password, display_name, locale?} -> {user, access, refresh}
POST /auth/login      {email, password}                        -> {access, refresh}
POST /auth/refresh    {refresh}                                 -> {access}
POST /auth/logout     {refresh}                                 -> 204   (blacklist refresh)
GET  /me                                                        -> {id, email, display_name, locale, children:[...]}
POST /consent         {child_ref?, consent_type, method}        -> {id, granted_at}
```

- Use **SimpleJWT** (`rest_framework_simplejwt`) with `ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True`; access ~15 min, refresh ~14 days. Add the token blacklist app + migration.
- Email verification: **stubbed** in dev — generate a token and log the verify link to the console (`logger.info`). A real email provider is later.
- `ConsentRecord` model (§17.2):

```python
# apps/accounts/models.py
class ConsentRecord(UUIDModel):
    parent = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="consents")
    child  = models.ForeignKey("profiles.Child", null=True, blank=True, on_delete=models.CASCADE)
    consent_type = models.CharField(max_length=64)          # e.g. "data_collection_v1"
    method = models.CharField(max_length=64)                # e.g. "checkbox_attestation"
    granted_at = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    evidence_ref = models.CharField(max_length=255, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
```

- **Gate:** child creation must 400/403 if no active `ConsentRecord` of the required `consent_type` exists for the parent. Enforce in the Child create serializer/view, **server‑side** (never trust the client).

### 4.2 Backend — `profiles` app (child CRUD)
Endpoints:
```
GET    /children                 -> [Child]
POST   /children                 {first_name, birthdate, interests[], fears[], avoid_topics[], intensity_ceiling, session_cap_min} -> Child
GET    /children/{id}            -> Child
PATCH  /children/{id}            -> Child
DELETE /children/{id}            -> 204   (cascade all child-linked data)
```

- `ChildSerializer` validation: `birthdate` yields age 3–7 (warn but allow outside for testing, flag in response); `intensity_ceiling` 0–3; `session_cap_min` 5–30; `interests/fears/avoid_topics` are lists of short strings (max length each, cap count at ~10). Seeds default to `[]`, never `null`.
- **Permission** `IsParentOwner`:

```python
# apps/common/permissions.py
class IsParentOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        parent_id = getattr(obj, "parent_id", None) or getattr(getattr(obj, "child", None), "parent_id", None)
        return parent_id == request.user.id
```

- Apply `IsAuthenticated & IsParentOwner` to all child‑scoped views. Confirm `on_delete=CASCADE` on every FK pointing at `Child` so DELETE leaves no orphans.
- **Throttling** (§18.2): DRF `UserRateThrottle` + `AnonRateThrottle` (e.g. 1000/day user, 100/day anon; tighter on `/auth/*`).

### 4.3 AI — prompt‑assembly module (`apps/safety/prompting`)
Pure, deterministic, unit‑tested functions (NOT wired to any generation today):

```python
# apps/safety/prompting/assembler.py
def system_identity_layer() -> str: ...          # immutable Primer + companion persona (from a prompt_version seed row)
def safety_policy_layer(child) -> str: ...        # generated from child.age_band, intensity_ceiling, avoid_topics
def assemble(child, *, memory=None, objective=None, plan=None, running_summary="", turn=None) -> list[PromptLayer]:
    """Returns ORDERED, LABELED layers per PRODUCT.md §19.1. Memory/turn labeled as DATA, never instructions."""
```

- Seed one `prompt_version` row (§17.4) holding the immutable system text; the assembler reads the active version.
- The safety layer MUST include the child's `intensity_ceiling` and each `avoid_topic` verbatim, and phrase guidance by `age_band` (3–4 vs 5–7).
- Unit tests in `apps/safety/prompting/tests/` prove determinism + that ceiling/avoid‑topics appear.

### 4.4 Frontend — auth, onboarding, parent gate
- **Auth store** (Zustand): tokens in memory + refresh; a silent‑refresh interceptor on the API client; `ProtectedRoute` wrapper for `/parent/*`.
- **Screens** (`src/routes/parent/`): `Signup.tsx`, `Login.tsx`, `Onboarding.tsx` (multi‑step: account → consent → add child), `ParentGate.tsx`.
- **Onboarding flow** (§4.2): create account → **consent step** with plain‑language disclosure + explicit attestation checkbox → **add‑child form** ("What does {name} love? What are they a little scared of? Anything to avoid?" + a friendly intensity slider + session‑cap picker). Optional fields skippable.
- **Parent gate** (§14.2): entering `/parent` requires an adult challenge (e.g. "enter the year you were born" or a small arithmetic prompt) that a young child can't pass. Store a short‑lived "parent‑verified" flag.
- Regenerate the typed API client (`make schema && make client`) after endpoints land, so all calls are typed.

## 5. Key stubs
```python
# apps/accounts/serializers.py (shape)
class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    class Meta: model = User; fields = ("email","password","display_name","locale")
```
- `/me` returns nested children via `ChildSerializer(many=True)`.
- Consent check helper: `require_consent(parent, "data_collection_v1")` raising `PermissionDenied` if absent.

## 6. Do NOT do today
- ❌ No companion creation UI/endpoint (Day 3). ❌ No story generation/beats. ❌ No billing. ❌ No live safety runtime / memory. ❌ Do NOT wire the prompt assembler into any live generation yet — module + tests only. ❌ Don't store the intensity slider as free text — use the 0–3 int.

## 7. Acceptance criteria (done when ALL true)
1. `signup` creates a parent + returns tokens; `login` authenticates; `refresh` rotates and **blacklists** the old refresh; `logout` invalidates; `/me` returns the parent + nested children.
2. Consent is recorded via `POST /consent` and a child **cannot** be created without an active consent record (server‑enforced; prove with a request that omits consent → 4xx).
3. Child CRUD works; validation rejects bad ceilings/caps and coerces seeds to lists; PATCH updates; seeds persist.
4. `DELETE /children/{id}` cascades — after deletion, **zero** rows referencing that child remain in any table (verify in psql).
5. `IsParentOwner` returns 403 when parent A requests parent B's child; throttling returns 429 past the limit.
6. Onboarding completes end‑to‑end in the UI; the parent gate blocks entry to `/parent` without passing the adult challenge; `ProtectedRoute` redirects unauthenticated users.
7. Prompt‑assembler unit tests pass: deterministic layers for a fixed child; `intensity_ceiling` + every `avoid_topic` present in the safety layer; age‑band changes phrasing.
8. OpenAPI schema regenerates cleanly; TS client updated and used by the frontend; `make lint && make test` green for both stacks; **CI green**.

## 8. Founder manual test
Sign up → hit the parent gate → complete onboarding, creating a child with 2 loves + 1 fear + 1 avoid‑topic → confirm in `/admin/` the `Child` and `ConsentRecord` exist and consent precedes the child. Delete the child; confirm it's gone. In a fresh incognito window, try to open `/parent` without logging in (blocked) and try creating a child via `curl` without consent (rejected).

## 9. End state to hand to Day 3
Parent auth (JWT signup/login/refresh/logout/me with blacklist), COPPA‑lite consent gating child creation, child‑profile CRUD with personalization seeds + owner‑scoped permissions + throttling, a working parent gate + protected routes, and a tested, deterministic prompt‑assembly module (system + per‑child safety layers reading a seeded `prompt_version`). Still **no** companion creation, **no** story loop, **no** live safety/memory/media.
