# Cleanup — report

> **Session:** hygiene pass against `Docs/Build/Cleanup.md`, 2026-08-14. Branch `main`.
> **Filed here** rather than at the `Docs/Build/Cleanup-report.md` the brief named — the founder
> moved it to sit with the other `*-report.md` files.
> **Scope honoured:** the GATED "remove the multi-provider seams" section was **not** performed. The
> router still has four providers and the image gate still has two moderators.

Every deletion below is unreferenced by proof, not by impression. Every candidate that could not be
proved dead is in **Kept, and why** with the reference that keeps it alive.

---

## 0. How things were proved

| Tool | Command | Result on the final tree |
|---|---|---|
| ruff (unused imports / redefinitions / commented-out code) | `ruff check --select F401,F811,F841,ERA001 .` | 0 F401/F811/F841. 4 ERA001, all false positives (see below) |
| vulture | `vulture backend/apps backend/config backend/tests backend/conftest.py evals --min-confidence 60` | every remaining hit is a Django/pytest declarative false positive |
| deptry | `deptry backend --requirements-files backend/requirements.txt --requirements-files-dev backend/requirements-dev.txt --ignore DEP003,DEP004` | 6 DEP002, all false positives (see §2) |
| grep | `grep -rn '\b<name>\b' backend frontend/src evals docs Docs` per symbol | quoted per item below |
| npm | manual usage grep of every `package.json` entry against `frontend/src` + config files | all 5 deps and all 24 devDeps referenced |

`vulture` and `deptry` were installed into `.venv` for this pass and uninstalled afterwards; neither
was added to `requirements-dev.txt` (they are one-off audit tools, not part of the build).

**The four ERA001 hits are not commented-out code** and were left alone: three are example env-var
values inside explanatory comments (`config/settings/base.py:740`, `:842`, and a section header in
`apps/memory/tests/test_consolidation.py:319`) and one is the English gloss above an injection regex
(`apps/safety/scrub.py:169`).

---

## 1. Deleted

### 1.1 Credentials removed from `.env`

| Item | Proof it was dead |
|---|---|
| `OPENROUTER_API_KEY=sk-or-v1-…` | `AI_PROVIDER_ORDER=google`; `base.py:386` reads it with `default=""`, and `base.py:654-655` only consults it when the router is asked for the `openrouter` provider. Nothing in the deployed configuration selects that provider. |
| `ANTHROPIC_API_KEY=` (empty) | Same shape — `base.py:381`, `default=""`. It held no credential; the empty line was a leftover of the pre-Vertex box. |

Both were replaced by a four-line comment saying where they went and how to get them back
(`.env.example` still documents both, one comment character away). `.env` is gitignored (`.gitignore:6-7`)
and has never been tracked.

> **`OPENROUTER_API_KEY` must still be revoked in the OpenRouter dashboard — see §3.** Deleting the
> line stops this repo using the key; it does not stop anyone who has a copy of it.

### 1.2 Dead code

Each was checked with `grep -rn '\b<name>\b' backend frontend/src evals docs Docs`; the "references
found" column is the complete result.

| Symbol | File | References found |
|---|---|---|
| `has_consent(parent, consent_type)` | `backend/apps/accounts/consent.py:73` | its own definition only. The live paths are `active_consent` and `require_consent` in the same module. |
| `forget_client()` | `backend/apps/common/vertex.py:113` | its own definition only. Docstring claimed "for tests and for a credential rotation"; no test and no rotation path called it. |
| `progress_for(child, objective)` | `backend/apps/learning/engine.py:402` | its own definition only. The live write is `engine.py:686`, which does its own `select_for_update().get_or_create()`. |
| `begin_adventure(child, *, session)` | `backend/apps/story/orchestrator.py:723` | its own definition, plus the Day-4 build brief that specified it (`docs/Build/Day4.md:63`, a historical document, left alone). The async entrypoints that are actually used are `generate_beat`, `ensure_current_beat`, `prepare_next_beat`, `follow_media`, `stream_media`, `attach_media`; the sync `resume_or_create_adventure` superseded it. |
| `window_open(child, *, controls)` | `backend/apps/story/wellbeing.py:116` | its own definition and the module docstring that advertised it. `gate_for` does the same `controls.time_window.is_open()` check *and* produces the child-facing message, and `gate_for` is what the views call. |
| `session_minutes(session)` | `backend/apps/story/wellbeing.py:206` | its own definition. The two other hits for that string are a dict key in `analytics/internal.py:191` and `analytics/metrics.py:467`, unrelated. |
| `avoid_topics_for(child)` | `backend/apps/profiles/settings.py:298` | its own definition. Docstring claimed "shorthand for the one setting two safety modules read on every beat"; both of them read `settings_for(child).avoid_topics` directly. |
| `Flag.is_bool` | `backend/apps/common/flags.py:81` | its own definition. |
| `ESCALATION_SEVERITIES` | `backend/apps/safety/incidents.py:33` | its own definition. **Worth reading twice**: its comment said "read here rather than at the call sites so 'what counts as serious' has one definition", and no call site read it. Escalation is decided on the *action*, not the severity — `pipeline.py:110` (`escalates = action == ESCALATE`), used at `pipeline.py:543`, and `pipeline.py:281` escalates unconditionally on the `CRITICAL` distress path. The constant asserted a rule the code does not implement. Removing it is behaviour-neutral and removes a false statement from a safety module. |
| `debug_task` | `backend/config/celery.py:22` | its own definition. The Celery-cookiecutter scaffold; the product's own liveness probe is `apps.common.tasks.ping`, which is what `make ping` runs. |
| `useSessionStore` + the whole file | `frontend/src/store/session.ts` | nothing imported `@/store/session` or the hook. The file's own docstring calls it a "Stub on Day 1"; server state went to TanStack Query and theme/world state went to `design/worlds.ts`. `zustand` stays a used dependency via `store/auth.ts:24`. |
| `themeAttribute` | `frontend/src/design/tokens.ts:15` | its own definition. It existed so nobody would hardcode a `data-theme` string; `design/worlds.ts:115` and `routes/parent/DashboardShell.tsx:68` both hardcode one. Same category as `ESCALATION_SEVERITIES`: a mapping that documents a discipline the code does not follow. |
| `Tokens` (type alias) | `frontend/src/design/tokens.ts:101` | its own definition. |

Two import lines became unused as a consequence and were removed with them:
`collections.abc.Sequence` (`profiles/settings.py:37`) and `ChildSettings` (`story/wellbeing.py:65`) —
both caught by `ruff F401`, not by eye.

### 1.3 Stale config text

| Item | Why |
|---|---|
| `Makefile:79` — `hello-beat … (needs an ANTHROPIC_API_KEY or OPENROUTER_API_KEY)` | Pre-Vertex. Now "needs live credentials for the configured provider", which is true under any `AI_PROVIDER_ORDER`. |
| `Makefile:82` — `smoke … (needs OPENROUTER_API_KEY)` | Same. Now "needs live provider credentials". |

### 1.4 One docstring corrected, not deleted

`apps/story/wellbeing.py`'s module docstring named `window_open` as one of the two questions the
module answers. It now names `gate_for`, and the window's semantics (the wrapping 19:00–07:00 case,
the timezone caveat) moved from the deleted function's docstring onto `gate_for` so nothing was lost.

---

## 2. Kept, and why

### 2.1 The provider-agnostic seams — kept, untouched, as instructed

`AnthropicProvider` (`story/ai/router.py:353`), `OpenRouterProvider` (`:435`),
`OpenAIProvider` (`:655`), `GoogleProvider` (`:798`), `OpenAIEmbeddingProvider`
(`media/adapters/embedding.py:101`), the `local-hash` embedder (`:176`),
`ClaudeImageModerator` (`media/moderation.py:90`) and `GeminiImageModerator` (`:163`) are all
present and unmodified. So is the `openai` package — it is the OpenRouter client, per the comment
that already sits above it in `requirements.txt`. The gated section of the brief was not performed
and is not proposed here; see §3.

### 2.2 Things that looked dead and are not

| Looked dead | Actually referenced by |
|---|---|
| `django-cors-headers`, `psycopg`, `django-celery-results`, `uvicorn`, `django-storages`, `boto3` (deptry DEP002 ×6) | All six are consumed by name rather than by import: the first three via `INSTALLED_APPS`/`MIDDLEWARE`/`DATABASES` in `config/settings/base.py`, `django-storages`+`boto3` via `apps/media/storage.py` and the `STORAGES` setting, and `uvicorn` as the server itself (`Makefile:71`, `infra/Dockerfile.backend:24`, `ci.yml:112`). deptry cannot see string-configured dependencies. **No Python dependency was removed.** |
| `GOOGLE_GENAI_USE_VERTEXAI` — the only `.env.example` variable with zero code references | Deliberate, and already documented in place: it is read by the google-genai SDK for ad-hoc scripts. The app passes `vertexai=True` explicitly (`apps/common/vertex.py`). |
| `"openrouter:nvidia/nemotron-3-super-120b-a12b:free"` in `analytics/pricing.py:73` | `apps/analytics/tests/test_cost.py:52`. It is the priced-at-zero free-route case, and the table's own comment explains why a known zero beats an unknown. |
| the `google/gemma-4-26b-a4b-it:free` example in `launch_check.py:223` | It is the worked example of the F-3 finding — the row that *used* to say OK about a `:free` model. `apps/common/tests/test_launch_check.py:139` asserts on that text. |
| every `admin.py` class, `apps.py` AppConfig, `Meta`/`list_display`/`fieldsets` attribute, `validate_*`/`get_*` serializer hook, management `Command`, custom-field hook (`common/crypto.py:159,166` — `get_prep_value`/`from_db_value`) and DRF permission/queryset hook (`common/permissions.py:27,51`) — the bulk of vulture's 60%-confidence output | Django and DRF resolve all of these by registration or by naming convention. Not touched. |
| every pytest fixture parameter vulture calls an "unused variable" (`no_redis`, `launchable`, `google_creds`, `timing_out_ai`, …) and every migration's `schema_editor` | Injected for side effects / required by the framework signature. Not touched. |
| `MessageParam` (`story/ai/router.py:44`) — vulture 90% | Used in a string annotation at `:407` (`cast("list[MessageParam]", …)`). ruff F401 correctly did not flag it. |
| all 5 frontend dependencies and all 24 devDependencies | Each grepped against `frontend/src` and the four config files. The two thinnest are still real: `@types/node` is required by `vite.config.ts:2` (`import path from 'node:path'`), and `postcss` is consumed by `postcss.config.js` and by Tailwind's build rather than by any import. |
| `frontend/src/lib/api/schema.ts` exports (`webhooks`, `operations`) | Generated by `openapi-typescript` (`npm run client`). Never hand-edit. |
| `smoke_ai --pick-free-model` | Part of the OpenRouter development seam, which is kept. |

### 2.3 Candidates that need a human, deliberately not deleted

| Candidate | Why it is a judgement call, not a cleanup |
|---|---|
| `api.progress` (`frontend/src/lib/api/client.ts:525`), `useProgress` (`lib/api/queries.ts:342`), `queryKeys.progress` (`:27`) | A complete, correct, *unwired* binding for a live and tested backend endpoint (`GET /api/v1/children/{id}/progress`, §12.3). The three only reference each other, so it is dead by the letter — but deleting it removes an API surface rather than dead product logic, and the parent dashboard is the obvious future consumer. Your call. |
| ~~`themes` / `ThemeName` (`frontend/src/design/tokens.ts:11-12`)~~ — **decided: keep** (founder, 2026-08-14) | After `store/session.ts` went, these have no consumer. They are the typed mirror of a real CSS contract: all three themes exist and all three are live — `storyland` is the child's base, `worlds.ts:115` sets `data-theme="night"`, `DashboardShell.tsx:68` and `parent/ui.tsx:167` set `data-theme="calm"`. Two lines is a cheap price for the one place in TypeScript that states there are exactly three, and it is what a theme switcher would be built on. **Kept.** |
| `apps/safety/models.py:63` — *"`HIGH` and above is what `escalation.notify_parent` acts on"* | Now that `ESCALATION_SEVERITIES` is gone, this is the last place that describes escalation as severity-driven. The code drives it off `action == ESCALATE`. The two are closely aligned in practice, but the sentence is a claim about safety semantics and I did not want to rewrite one unilaterally in a hygiene pass. Worth one minute of your attention. |

### 2.4 Nothing the Day-1–15 audit proved clean was touched

No change in this pass reaches cross-account isolation, deletion, ceilings, the fallback path, the
budget breaker, the ship gate, or the F-1/F-2/F-3 fixes. The two safety-module edits
(`incidents.ESCALATION_SEVERITIES`, `wellbeing.window_open`/`session_minutes`) removed symbols with
zero call sites; the safety pipeline, the boot guards and `launch_check` are byte-identical.

`manage.py launch_check` after the `.env` edit resolves every AI route on the service account with
no API key present anywhere — which is the point of removing them:

```
OK    safety.grader_reachable   google is configured for gemini-2.5-flash-lite
OK    safety.grader_is_separate the grader (gemini-2.5-flash-lite) is a different model from the writer (gemini-2.5-flash)
OK    safety.image_gate         IMAGE_MODERATION=auto, and google-vision is configured to look at every generated picture
OK    privacy.provider_terms    every child-data model is on a route whose terms exclude training
                                (gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.5-flash-lite, gemini-embedding-001)
```

The run's one `FAIL` (`spend.breaker`, `INFERENCE_BUDGET_USD_PER_WINDOW` unset) and three `WARN`s
(`memory_key`, `MEDIA_STORAGE=local`, the manual restore-drill row) are pre-existing properties of a
development box and are unchanged by this pass.

---

## 3. Founder actions

1. **Revoke `OPENROUTER_API_KEY` in the OpenRouter dashboard.** The key (prefix `sk-or-v1-9708…`, so
   you can pick it out of the list) is out of `.env` but is still live at the provider until you
   revoke it. It sat in a OneDrive-synced folder for the whole build.
2. **Rotate the Vertex service-account key.** `GOOGLE_APPLICATION_CREDENTIALS` points at
   `C:/Users/DELL/.config/primer/vertex-sa.json`, which is correctly outside the repo — but the key
   was created and handled on a machine whose Desktop is OneDrive-synced. Rotate it in the GCP
   console and delete the old key version. (Restated from the Vertex build report; still open.)
3. **`ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env` are read by no code path** — grep returns nothing
   across `backend`, `evals`, `infra`, `.github`, `Makefile` and the docs. They are your record of
   the Django superuser login. **I left them in place on purpose**: deleting somebody's only written
   copy of a live password is not a cleanup. Move them to a password manager, then delete the two
   lines.
4. **`Docs.lnk` is tracked in git** (`git ls-files` lists it; added by `9c31012`, "Track docs/Build;
   fix .gitignore case-insensitive collision" — swept in by a `git add -A`). It is a Windows
   shortcut: binary, useless on CI and on any non-Windows checkout, and it is the one piece of
   root-level cruft in the tree. To drop it from the repo while keeping the shortcut on your
   Desktop:
   ```
   git rm --cached Docs.lnk && echo "Docs.lnk" >> .gitignore
   ```
   Not done here — it is a founder-authored file.
5. **`docs/Audit/Day1-15 AuditOutput.txt`** is a pasted chat transcript of the audit's closing
   summary, fully superseded by `docs/Audit/Day1-15-audit-report.md` next to it. Flagged, not
   deleted. (The other superseded doc the brief named, `docs/Test/Day10-manual-test.md`, was already
   removed in `c0ce720`.)
6. **Working-tree artifacts, all correctly gitignored, none deleted:** `backend/media/` is **66 MB**
   of locally generated illustrations and audio (170 files) backing rows in your dev database, and
   `backups/primer-20260813T022013Z.dump` is a **database dump** — both sitting in a OneDrive-synced
   folder. Nothing in the repo depends on either. Clearing `backend/media/` will break media URLs in
   your local dev DB; the dump is a copy of dev child data and belongs off this machine.
7. **The provider-seam decision (the brief's GATED section) — not proposed.** The reasoning in the
   brief still holds: Vertex has already shown friction (region 404s, prepay walls), the seams are
   tested, and `test_an_anthropic_deployment_is_unchanged` and the pairing-error tests exist
   precisely to keep the escape hatch working. Default stands: keep them.

---

## 4. Green

| Check | Result |
|---|---|
| `make lint` | ruff check + `format --check` clean on `backend` (234 files) and `evals` (9 files); eslint + prettier clean |
| `make test` | **1480 backend passed** (26m34s) + **183 frontend passed** (13 files) |
| `make evals` | **44 passed, 1 skipped** (1m28s) — the ship gate |
| `mypy` | **no issues found in 262 source files**; `tsc --noEmit` clean |
| `pip-audit -r backend/requirements.txt` | **No known vulnerabilities found** |
| `npm audit` (and `--omit=dev`) | **found 0 vulnerabilities** |
| `makemigrations --check` | **No changes detected** — no migration added or invalidated |
| `manage.py check` | **System check identified no issues (0 silenced)** |
| CI | **green, run `31769124062`** on `7c54c81` — all five jobs (Backend, Frontend, Evals, Dependencies, Backend image) |

Backend count is up from the 1464 in the Vertex-Embeddings report because the Vertex fix pass added
tests. **No test was deleted in this pass** — nothing removed here had one, which is itself part of
the proof that it was dead.
