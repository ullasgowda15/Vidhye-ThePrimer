# Day 12 Review Report — Parent Dashboard: Reports, Insights & Transparency
**Reviewed:** 2026-08-12 · **Commit:** `7798b9c` ("Day 12: parent dashboard — reports, insights and transparency") · **Reviewer:** Claude Code (combined review-then-fix session, both phases authorised by the founder)

> **Correction to the session brief.** Day 12 was described as "committed but not pushed". It **is** pushed — `origin/main` is at `7798b9c` and CI run **`31566210823` is green** (Backend 1005 passed, Frontend 122 passed, ruff clean, `makemigrations --check` clean, schema-staleness check clean, ASGI + Celery smoke-tests pass). `Docs/PROGRESS.md` still says "not yet reviewed" and the founder's note says "I stopped short of pushing"; both predate the push.

## Overall verdict
**FIX-THEN-SHIP — one P0.**

The architecture of this day is right, and in the place where it matters most it is right for the right reasons. Almost nothing in a report is generated: where the child went, the branch they took, the objective and how it went are rendered from `Beat`, `Choice` and the live `LearningProgress`, and the one sentence a model does write is re-checked before it is stored. Owner-scoping is genuinely watertight on all eight endpoints — I could not construct a cross-account read. `reach_for` is a good answer to the question Day 10 left open, and the parent surface really does render no number.

What fails is the layer above that: **three of the parent-facing strings are untrue or unreadable**, and none of them is caught by a test, because the tests assert shape and substrings rather than reading the sentence a parent would read. I ran a real sitting end-to-end and read the output. The weekly report told a parent their child had **reached a milestone this week** that was crossed three weeks earlier, and would keep saying so; the session report's "the choice that mattered" pasted the *model-facing* story-steering note into the parent's page verbatim; and the Insights page labelled a milestone **"Not covered yet"** on a card that, two lines below, said the child had practised it twice today.

The P0 is the first of those: a fabricated claim about a child's development, in the report §12.1 says must never be inflated. It is not a scoping or a security failure — it is exactly the failure this day's own docstrings spend two hundred lines arguing against, arriving through a baseline query that reads one row where it needed a per-objective index.

**All P0/P1 items were fixed in this session** (see *Fixes applied*), re-verified, and pushed.

## Scorecard

| # | Criterion | Verdict | Evidence (cmd/file → result) |
|---|-----------|---------|------------------------------|
| 1 | Per-session report generates after a session: specific, honest, with a **real** highlight | 🟡 → ✅ *(after fix)* | Generation is real and idempotent on `session_key`, with three independent routes converging on one row (`tasks.generate_report`, a redelivery, and the read-path `report_for_session`) — `test_a_second_generation_returns_the_first_report` proves one row survives two calls. Content is genuinely read off rows: I played a real five-beat sitting and checked each claim against the beats. `world/place = the Whispering Woods / the Hollow Oak` matches `adventure.plan`; `how_it_went = "Counting 2 things — straight away, on their own; counting 3 things — straight away, on their own."` matches the two `count_to_3` beats stamped at difficulty 0.10 and 0.25 (`demand_for` gives quantity 2 then 3 — the numbers that were in the pictures, which is only true because of the Day-10 anchor fix). Assisted and missed paths verified separately: `assisted=True` → "got there together" and never "on their own"; `correct=False` → "had a go — still new", with no failure word. **What failed:** `key_choice` — see **P1-1**. |
| 2 | Weekly report aggregates sessions/completion/skills/milestone/interest-trend/recommendation per §18.7 | ❌ → ✅ *(after fix)* | Shape matches §18.7 exactly (`week`, `sessions`, `completionRate`, `highlight`, `skills[]`, `milestone`, `interests_trend`, `recommendation` — asserted as an exact key set in `test_the_weekly_report_is_shaped_as_18_7_writes_it`), ISO Monday–Sunday weeks, `?week=` parsed strictly with a 400 on garbage rather than a silent fallback to this week. Completion is counted from real `ended_reason` values (2 sittings, one `completed` one `exit` → 0.5). **But the movement and milestone claims are measured against a single baseline row** and are false in the common case — **P0-1**, proven below. |
| 3 | Learning insights render per-domain, plain language, mapped to milestones — no grades/percentiles | 🟡 → ✅ *(after fix)* | No number reaches the parent, and this is enforced rather than asserted: `mastery`/`reach` cross the wire, `Insights.tsx` renders `status` + `summary` only, and `dashboard.test.tsx` scans `container.textContent` for `'%'`, `'0.64'` and `'64'` against a fixture pinned at `mastery: 0.64`. I checked that ban is not vacuous — the string `64` does not otherwise occur in the rendered DOM, so a page that rendered the estimate in any of the three readable forms fails. `StatusPill` has no bar, ring or percentage, and `STATUS_LABEL` has no fifth word meaning "behind" (`test_no_sentence_tells_a_parent_their_child_is_behind` sweeps a forbidden-word list). `reach_for` is the right answer to Day 10's open question and is asked of `demands` rather than re-deriving `COUNT_CEILING`: 0.64 / 0.84 / 1.0 for count_to_3/5/10, so 0.63 reads `mastered` on the short ladder and `practising` on the long one. **What failed:** the milestone *status* itself — **P1-2**. |
| 4 | Transparency log shows actual stories verbatim; **owner-scoped (parent B cannot read parent A's child)** | ✅ | **No cross-account read exists.** The scoping is structural: every child-scoped view inherits one `ParentChildView.child_for`, which resolves the child, runs `check_object_permissions` (`IsParentOwner` → `child.parent_id == user.id`) and then `require_consent`, **before** a report, memory, beat or progress row is read — so a wrong-family request cannot even learn that a sitting exists. `TranscriptView` checks the same permission against the adventure, whose `child.parent_id` is what `IsParentOwner` walks to. `report_for_session` additionally filters `child=child`, so owning the child named in the path is *not* enough to read another child's `sessionId` (`test_a_session_id_belonging_to_another_child_is_not_readable` → 404). All eight endpoints are covered in both directions by two real accounts with real tokens: owner → 200 ×8, second family → **403 ×8**, unauthenticated → **401 ×8**. I re-read each view for a path that skips `child_for` and found none; the three list endpoints all filter by `child`. Verbatim is real and thin by design — `transparency.py` reads `Beat`/`Choice` and returns them, `safetyStatus` is shown rather than hidden, and the client asserts an exact string match on narration (`node.textContent === NARRATION`, no ellipsis) so a client-side truncation would fail the suite. |
| 5 | Report content grounded in real session data — **no fabrication** | ❌ → ✅ *(after fix)* | The grounding machinery is well built where it exists: `_UNGROUNDING` is what makes `is_grounded` a check rather than a formality (without it `"the"` is a specific and every sentence passes — `test_a_common_word_cannot_ground_a_sentence` pins that), the model is asked for one sentence and nothing else, a session with nothing to point at never calls a provider, `HighlightUnavailable` covers every failure, and `highlight_source` is on the wire so the gate's firing rate is observable. **Three failures against this criterion**, all found by reading output rather than tests: **P0-1** (a milestone falsely claimed as reached this week), **P1-1** (internal prompt text presented as the choice's consequence) and **P1-3** (a sentence about our own scenery accepted as a "moment" and labelled `model`). |
| 6 | Parent dashboard IA + calm theme render across the pages | ✅ | `data-theme="calm"` on the shell, one switch, no component branching on surface. §12.10's four built sections are in the nav (`Reports`, `Weekly & growth`, `Insights`, `Transparency`) with Home and Children in the header; Settings/Privacy/Billing are named in a footer line rather than rendered as nav items that go nowhere — the right call for a product whose headline feature is honesty, and consistent with the day's "Do NOT do today" list. Routes are `child/:childId/...` rather than `:childId/...` so a child id can never collide with a sibling route. `Section` centralises loading/error/empty, and the parent surface *shows* errors (`role="alert"`) — the exact inverse of the child surface, asserted by a test. Frontend 122 tests green, `tsc` clean. |
| 7 | `make lint && make test` + CI green | ✅ | `make lint` → ruff check + `ruff format --check` + eslint + prettier, all clean. Backend `pytest` → **1005 passed**, exit 0 (local, against the Docker `db`/`redis`). Frontend `vitest` → **122 passed** (10 files). `npm run typecheck` → clean. `makemigrations --check --dry-run` → no changes. `spectacular` re-export → `frontend/openapi.json` byte-identical; `npm run client` → `schema.ts` byte-identical. **CI green on `7798b9c`** (run `31566210823`, all three jobs success). *Note: the first local `make test` reported 823 errors — every one a `django.db.utils.OperationalError` because Docker Desktop was not running. Environment, not code; re-run clean once `infra/docker-compose.yml` was up.* |

Counts (as found, before fixes): ✅ 3 · 🟡 2 · ❌ 2

## The P0, proven

Three `SessionReport` rows for one child, written as they would be written by real sittings:

| when | objective | status in that snapshot |
|---|---|---|
| 21 days ago | `count_to_3` | `mastered` |
| 9 days ago (**the most recent report before this week**) | `name_colours` | `practising` |
| this week | `count_to_3` | `mastered` |

`generate_weekly_report` returns:

```
WEEK:      2026-08-10/2026-08-16
MOVEMENT:  [('count_to_3', 'first practised this week')]
MILESTONE: {'name': 'Counting and quantity',
            'step': 'count a small group of visible things, up to three, …',
            'reached': True}
```

Both lines are false, and the second one is the serious one. `WeeklyReport.tsx` renders it as a card headed **"A step reached"**, reading *"Counting and quantity — Aarav can now count a small group of visible things, up to three."* The child crossed that step three weeks ago.

The cause is one query:

```python
baseline = (
    SessionReport.objects.filter(child=child, occurred_at__date__lt=start)
    .order_by("-occurred_at").first()
)
```

`_movement` and `_week_milestone` both index that **single report** and treat "this objective is not in it" as "there is no history". But §10.3's selector deliberately interleaves domains, so two consecutive sittings practise *different* objectives — which means the most recent report before any given week is usually about something else, and the baseline is empty for the objective actually being asked about. `_week_milestone` then reads a missing baseline as `NOT_STARTED`, compares it to `MASTERED`, and reports a crossing.

The module's own docstring says the design intent plainly — *"'it is mastered now and they played this week' would report the same milestone every week forever"* — which is very nearly what the implementation does. The existing test passes only because its helper (`_snapshot_last_week`) pins the *same* objective code into the baseline row, which is the one arrangement in which the bug is invisible.

**Assessment: P0.** It is a confident, specific, false claim about a child's development, on the surface §12.1 says must be honest and never inflated, repeated weekly. Criterion 5 fails.

## The two P1s that a test would not have found

**P1-1 — the parent is shown the model's story-steering note.** From the same real sitting, `key_choice` as stored and as rendered under *"The choice that mattered"*:

> Chose "Follow the sparkling river". At beat 3, Aarav chose "Follow the sparkling river". **The beat's own steer for what follows: if the child counts -> cross the river**

`_key_choice` appends `Choice.consequence_summary`, but that field is not a parent-facing string — `orchestrator.consequence_summary` writes it *for the next generation*, and it carries `beat.next_hint`, our internal instruction to the writer. So the line is (a) duplicated, (b) leaking machinery in machine register, and (c) the only place in the whole parent surface where the seams show. The existing test asserts a card label appears *somewhere* in the string, which this satisfies.

**P1-2 — a milestone the child is actively practising reads "Not covered yet".** Insights for the same child, one card:

```
Counting and quantity  [not_started]      → pill renders "Not covered yet"
    - emerging     Just starting to count a small group of visible things,
                   up to three… We have seen this 2 times so far.
    - not_started  Not covered yet — … count up to five…
    - not_started  Not covered yet — … count up to ten…
```

`milestone_rollup` takes the **least advanced** objective under the milestone, which is defended in the docstring ("so 'mastered' means counting to ten rather than counting to three") — and that argument is right at the top of the range and wrong at the bottom. The pairing that is supposed to rescue it, `furthest`, is empty until something is *mastered*, so it rescues nothing for the case every real child is in for their first months. The card contradicts itself, and it says "not covered" about something covered today. §12.3 asks for framing that is developmental and positive; this is neither, and it is also untrue.

**P1-3 — scenery alone grounds a "model" highlight.** `_scenery`'s docstring says it "never grounds a highlight *on its own* — `_child_contributions` comes first in the specifics set". `specifics` is a `set`, and `is_grounded` is a plain `any()`, so there is no "first". Probed on a sitting where the child contributed nothing:

```
MODEL CALLED: True
SPECIFICS: ['dawn', 'mossy', 'mossy riverbank at dawn', 'pip', 'riverbank']
SCENERY-ONLY SENTENCE PASSES is_grounded: True
STORED SOURCE: model
STORED HIGHLIGHT: "Aarav loved the mossy riverbank at dawn — full of wonder."
```

The report page then prints *"Written from this session by our AI, and checked against what actually happened."* under a sentence about our own prose. The honest answer for that sitting is the `quiet` branch, which already exists and is already good. Note that scenery in the specifics set has **exactly one** effect — it lets scenery-only sentences through — because any sentence that names both scenery *and* something the child did already passes on the child's half.

## What's working well

- **The scoping check runs before the read, not around it.** `child_for` resolves → `check_object_permissions` → `require_consent` → *then* query. §11.10's point is that the sensitive thing is the fact of the data, not only the payload, and this ordering is the version of that which survives someone adding a ninth endpoint in a hurry. The test file is deliberately repetitive for the same reason, and that repetition is correct.
- **The report is stored, the week is computed.** Both arguments are right and they are opposite arguments, which is the tell that they were actually thought about: a report is a claim about one afternoon and must not be quietly rewritten by next month's catalog; a weekly digest is an aggregation and a stored copy would be a second thing that can disagree with the rows under it.
- **`reach_for` closes Day 10's open question by rejecting its premise.** Not per objective, not per ladder — the number is not shown. Dividing by what the objective can actually reach, and thresholding into words, is the only reading under which "finished counting to three" does not display as 64% of something.
- **The read-path fallback.** `report_for_session` generates a missing report on demand, so a broker outage on the evening a child played cannot leave a permanent hole. It is idempotent, it is on a request a parent made deliberately, and it is off the child's path.
- **`highlight_source` on the wire.** Storing *and publishing* which of three kinds of sentence a parent is reading is the strongest honesty move on this surface, and it doubles as the only production window on the grounding gate's firing rate. The admin page is read-only in every field for the same reason.
- **The post-session gate is real and it works.** `tasks.generate_report` waits on `learning_updated_at` / `interests_updated_at` rather than trusting enqueue order. Probed directly: both stamps null → `Retry` raised and no row written; retries exhausted → writes anyway and returns `stale` with a warning; a second call → `already`; a deleted sitting → `missing`. Exactly as documented. (It had **no test** — **P1-4**, fixed.)
- **`transparency.py` is thin and says so.** "If this file ever grows a filter, the feature is gone and only the claim is left" is the correct standard, and showing `safety_status` — the uncomfortable choice — is what makes §12.6 an answer rather than a promise.

## Punch list

### P0 — blocking

- **P0-1 — The weekly report claims a milestone reached this week that was crossed weeks ago, and mis-reports movement.** ✅ **FIXED.** Proven above. `generate_weekly_report` takes the single most recent `SessionReport` before the week as *the* baseline; `_movement` and `_week_milestone` both read it per objective, so any objective absent from that one row has no history — which, given §10.3's domain interleaving, is the normal case. Symptoms: `movement` says `"first practised this week"` for a skill practised for months, and `milestone` reports a step as `reached: True` every week the child touches it. **Fix applied:** the baseline is now a **per-objective index** built by walking reports before the week newest-first and taking the most recent snapshot of each code the child actually practised this week, stopping as soon as every code is found (bounded by `BASELINE_SCAN`). Two regression tests added, both of which fail against the old code.

### P1 — should fix before shipping

- **P1-1 — `key_choice` pastes internal prompt-steering text into the parent's report.** ✅ **FIXED.** `Choice.consequence_summary` is written for the *next generation*, not for a parent, and carries `beat.next_hint` verbatim ("The beat's own steer for what follows: if the child counts -> cross the river"), plus a duplicate of the choice label. **Fix applied:** the consequence is now the opening sentence of the **next beat's own narration** — what the story actually did about the choice, in the story's words, which is both true and the register the rest of the page is in. Test added asserting the report carries no `next_hint` and no internal steer language.
- **P1-2 — A milestone being actively practised is labelled "Not covered yet".** ✅ **FIXED.** `milestone_rollup` takes the least advanced objective under the milestone, and with `count_to_5`/`count_to_10` untouched that is `not_started` for a child who counted to three twice today; `furthest` is empty until something is mastered, so the intended pairing does not rescue it. **Fix applied:** the milestone floors at `emerging` once **any** objective under it has evidence, while still taking the minimum above that — so `mastered` still means all of them, and "not covered yet" now means what it says. Test added.
- **P1-3 — Scenery alone grounds a model highlight, and `highlight_source` then over-claims.** ✅ **FIXED.** Proven above. **Fix applied:** the grounding set is the child's own contributions only; a sitting the child put nothing into no longer calls a provider at all and reports itself as `quiet`. `_scenery` is removed rather than left as a set the check ignores. Two tests added — one that the gate rejects a scenery-only sentence, one that no provider call is made.
- **P1-4 — `analytics/tasks.py` has no test.** ✅ **FIXED.** The gate is the mechanism the whole snapshot story rests on — it is what makes "moved on this week" measurable — and nothing pinned any of its four outcomes. I verified all four by hand for this report; they now have a test file (`apps/analytics/tests/test_tasks.py`) covering wait, give-up-and-write-`stale`, `already` and `missing`.

### P2 — improvements (non-blocking, not fixed in this session)

- **P2-1 — `SessionReport.skills` stores `mastery` and `status`, and the model docstring says it does not.** `models.py` states "**No mastery number is stored here**" and documents the column as `[{code, domain, milestone, asks, unaided, assisted, retried}]`; `_skill_snapshot` writes `mastery` and `status` as well, deliberately and for a good reason (`reports.py`'s docstring explains it — movement must be measured against something recorded at the time). The two docstrings contradict each other. The behaviour is right; the model docstring is wrong and should be corrected, because in this codebase the docstring is how the next person learns the rule. *(No parent sees these: `SkillEvidenceSerializer` omits both.)*
- **P2-2 — The weekly report counts stored reports, not sittings.** `sessions` and `completionRate` are computed from `SessionReport` rows in the week. A sitting whose report was never generated — broker down, and the parent never opened that specific session page, so the read-path fallback never fired — is invisible to the week. The dashboard would say "3 stories" for a week with 5. Cheapest honest fix: backfill missing reports for the week on read, or count sittings from `PlaySession` and let the skills half stay report-derived.
- **P2-3 — A report describes only the first adventure of a sitting.** `generate_session_report` takes `session.adventures.order_by("started_at").first()`, and `evidence`/`beats`/`world` all come from it. Today one sitting holds one adventure, so this is latent — but `resume_or_create_adventure` already accepts a `session=` argument and `_finalise_session`'s own docstring anticipates the shared sitting arriving. When it does, this silently reports a fraction of the afternoon.
- **P2-4 — `/progress` and `useProgress` are built and unused.** The endpoint is in the brief's API list and is correct, but no page consumes it — Insights reads `/insights`, which returns the same objectives grouped. Either wire it to a page or record it as an API-surface-only deliverable, so the next person does not assume it is load-bearing.
- **P2-5 — `_child_contributions` and `memory.prompts._choice_line` disagree about what a tap meant.** A typed word on a `choose` beat is `said` to the report and `answered` to the consolidation digest (visible in the scripted highlight, which produced *"Aarav counted Sparkle of them, unprompted"*). `transparency._answer` agrees with the report. Pre-existing Day-9 behaviour, surfaced by Day 12 reading the digest; harmless today because both are internal, but the three readings should be one function.
- **P2-6 — Test-count claims are off.** `Docs/PROGRESS.md` and `Docs/Day12-Build.txt` both say "1013 backend tests"; the real number is **1005**, locally and in CI.
- **P2-7 — Working-tree hygiene, same pattern as Day 11's P2-6.** `Docs/Day12-Build.txt` sits untracked alongside the day's work. Consistent with previous days (the `DayN-Build.txt` transcripts have never been committed deliberately), recorded rather than acted on.

## Scope & risk notes

**Scope discipline: clean.** No billing, no settings or controls, no privacy centre — `DashboardShell` names all three in a footer sentence instead of linking them, which is the honest way to leave a gap. No child-facing change: the only touch outside `apps/analytics/` and `routes/parent/` is the fourth `.delay` in `_close_out_session`, in its own `try` block like its three siblings, plus the scripted provider gaining a `SessionHighlight` answer. Day-7 safety, Day-9 memory, Day-10 mastery and Day-11 personalisation are all untouched and all green inside the 1005.

The three endpoints beyond the brief (`reports/sessions`, `transcripts`, `insights`) are each the index a §12.10 page needs and are justified in `views.py`'s module docstring. Accepted.

**Consent gates every dashboard read.** A parent whose consent has lapsed cannot read their own child's transparency log. Correct default and correctly flagged by the build for revisiting when Day 13's privacy centre lands — recording it here so it is a decision rather than a discovery.

**Risk carried forward:** P2-2 is the one most likely to be noticed by a real parent, because it makes the weekly report quietly under-count a week in exactly the situation (a worker outage) where nobody is looking.

## The two flagged deviations from §18.7

**Both accepted.** They are documented in `WeeklyReport`'s docstring and in `PROGRESS.md`, which is the right place for them.

- **`movement` is a phrase, not `"+0.2"`.** Correct call, and it is the same argument `skills.py` makes for not showing mastery at all: the delta is on the difficulty scale, its ceiling differs per objective, and 0.2 means something different for `count_to_3` than for `count_to_10` — so `"+0.2"` is a number a parent cannot interpret without the curriculum in front of them. The field is still a string, so this is additive under §18.8 and breaks no client. §18.7 is captioned "Example", and §12.2 asks for "any mastery movement" without prescribing a form.
- **No `new_words` entry.** Correct call, and the stronger of the two. §18.7's example shows `{"domain":"vocabulary","new_words":["brave","mossy","lantern"]}`; we do not record which words a child met in a story, so the only way to populate it would be to derive it from narration — i.e. to guess which words were new to this child. On the one surface whose entire value proposition is that it does not make things up, shipping a guessed list would be the worst possible trade. Ship the field when word exposure is actually tracked.

One note for the founder rather than a finding: both deviations mean `docs/PRODUCT.md` §18.7 now describes a response the API does not return. §18.8 promises additive-only change within `/v1`, and these are additive, but the example should be amended when §18.7 is next touched so the spec and the implementation stop disagreeing on paper.

## Unverified items (and why)

- **What a real model writes into the highlight.** Every generated sentence in this audit came from `ScriptedAIProvider`, which now answers `SessionHighlight` by quoting something the child actually did — meaning CI exercises the model path end-to-end, but against a provider that is *trying* to pass the gate. The build flagged this itself and it is the right thing to flag. Two things are worth watching in the founder manual test: how often `is_grounded` fires against a real model (visible as the `data` rate in the admin), and whether the sentences read warm rather than clinical. No `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY` in this environment, so `make smoke-story` could not run.
- **Whether the reports read as *true* to a parent who was in the room.** I verified every claim against the rows that produced it. Whether "Counting 3 things — straight away, on their own" describes the afternoon the parent remembers is the thing only §8 of the brief can settle.
- **Long-run weekly behaviour.** Everything here is one to three sittings deep. P0-1 only appears once a child has a history *and* the interleaving has moved on — which is to say, from about the third week of real use. That is worth assuming there are others of the same shape; the fix's regression tests are written to be the kind that would have caught it.
- **Visual review of the calm theme.** Contrast tokens are inherited from Day 11's fixed palette and are not re-measured here; nothing in this day introduced a new colour.

---

## Fixes applied (Phase 2 of this session)

Per the founder's instruction, the punch list was fixed in the same session, after this report was written. Nothing the audit marked ✅ was touched: owner-scoping, the transparency log, `reach_for`, the storage model and the IA are unchanged.

| Item | Status | Files |
|---|---|---|
| P0-1 — weekly baseline is one row, not a per-objective index | ✅ Fixed | `backend/apps/analytics/reports.py`, `backend/apps/analytics/tests/test_reports.py` |
| P1-1 — `key_choice` leaks the model-facing steer | ✅ Fixed | `backend/apps/analytics/reports.py`, `backend/apps/analytics/tests/test_reports.py` |
| P1-2 — a practised milestone reads "Not covered yet" | ✅ Fixed | `backend/apps/analytics/skills.py`, `backend/apps/analytics/tests/test_skills.py` |
| P1-3 — scenery alone grounds a `model` highlight | ✅ Fixed | `backend/apps/analytics/reports.py`, `backend/apps/analytics/tests/test_reports.py` |
| P1-4 — the post-session gate has no test | ✅ Fixed | `backend/apps/analytics/tests/test_tasks.py` (new) |
| P2-1 … P2-7 | Not fixed — non-blocking, carried forward | — |

### Re-verification, re-measured rather than assumed

**P0-1.** The exact history that produced the false claim — `count_to_3` mastered 21 days ago, an unrelated `name_colours` sitting 9 days ago (so it is the last report before the week), `count_to_3` again this week — now returns `movement = "moved on this week"` and `milestone = None`. Both regression tests were proved to *fail* against the old single-row baseline before the fix was kept. The walk is bounded and short-circuits: for the ordinary child it reads one or two rows, because it stops as soon as every objective practised this week has a baseline.

**P1-1.** `key_choice` from a real played sitting, before and after:

> **was:** Chose "Follow the sparkling river". At beat 3, Aarav chose "Follow the sparkling river". *The beat's own steer for what follows: if the child counts -> cross the river*
> **now:** Chose "Follow the sparkling river". What happened next: *Because you chose Follow the sparkling river, the river goes quiet, and 3 little frogs are sitting along the fallen log in the sun.*

The second half is the next beat's own narration — story the child was actually told, quotable back to the transcript, and the test asserts exactly that (plus that no beat's `next_hint` appears anywhere in the string).

**P1-2.** The same child's Insights, before and after: `Counting and quantity [not_started]` → `Counting and quantity [emerging]`, with the untouched milestones still correctly `not_started`. The honest reading above the floor is unchanged — one mastered rung out of three is still not a mastered milestone, which the new test pins in both directions.

**P1-3.** The specifics handed to the model for a real sitting no longer intersect the beats' `scene` at all (asserted as a set intersection against `characters_present` + `setting`, so it cannot rot into a substring coincidence), and a sitting with scenery but no contribution from the child now reports `quiet` **and makes no provider call** — previously it called the model and could store a sentence about our own prose as `model`. *One care taken while writing this test:* the first version asserted `is_grounded` rejects "Aarav loved the mossy **river**bank at dawn", which passed — because `river` is a genuine child specific from the card they took, and the check is deliberately substring-based. The assertion was wrong, not the fix; it now tests the property that actually changed.

**P1-4.** All four gate outcomes pinned: both stamps null → `Retry` and no row written; `GATE_RETRIES` exhausted → the report is written anyway, returns `stale`, and logs `analytics.report_stale`; a redelivery → `already` with still one row; a deleted sitting → `missing`.

**Suites after the fixes:** `make lint` clean (ruff check + `ruff format --check` over **195 files**, eslint, prettier) · backend `pytest` **1016 passed**, exit 0 (1005 before, plus the 11 regression tests above) · frontend `vitest` **122 passed** (10 files) · `npm run typecheck` clean · `makemigrations --check --dry-run` → "No changes detected" · `spectacular` re-export and `npm run client` → `openapi.json` and `schema.ts` both byte-identical, since the fixes touched no serializer · **CI green on the fix commit.**

**Nothing the audit marked ✅ was touched.** The eight endpoints, `IsParentOwner`, `ParentChildView.child_for`, `transparency.py`, `reach_for`, `status_for`, the storage model, the migration and the whole frontend are unchanged; the diff is `reports.py`, `skills.py` (six lines and a docstring), `prompts.py` (a docstring), and tests.

### Note on side effects of this review

Five temporary probe files were written under `backend/apps/analytics/tests/` to gather the evidence quoted above — the weekly-baseline history, the scenery-grounding trace, the four gate outcomes, and the full before/after read of a real report. All five were deleted; `git status` confirms the tree holds only the intended fixes plus the pre-existing untracked `Docs/Day12-Build.txt` recorded in P2-7. Probes ran against a **separate** `primer_probe` test database so they could not disturb the suite running alongside them, and no application data was touched. Docker Desktop was started to bring up the local `db`/`redis` containers, which were down when this session opened — that is why the first `make test` reported 823 connection errors.
