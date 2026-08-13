# Day 14 Review Report — Analytics, Cost Instrumentation, Accessibility & Polish
**Reviewed:** 2026-08-13 · **Commit:** `aec0261` ("Day 14: analytics, cost instrumentation, accessibility and polish"), with `b91acaf` (mypy) and `9c944b5` (docs) on top · **Reviewer:** Claude Code (combined review-then-fix session, both phases authorised by the founder)

> **State as found.** `main` is at `9c944b5`, pushed, and CI run **`31627539893` is green** on `b91acaf` (`gh run view` → `conclusion: success`). Nothing needed pushing before the review began.

## Overall verdict

**READY, with seven P1s and no P0** (six found in the audit; the seventh found by CI on the fix commit — a Day-14 test that fails for about five hours out of every twenty-four, and had never yet been run in that window). The two properties the brief singles out both hold, and both hold for the right reason rather than by luck.

**No child data is in telemetry.** I read every one of the eleven emission sites rather than trusting `_clean`, and the props are shape throughout: an age band and three list *lengths* on `child.created`; a world key, an objective code and a rounded difficulty on `adventure.started`; a positional key, a latency and two booleans on `choice.made`; four classifier labels and nothing from `detail` on `safety.incident`. The two places a child's own words exist in this product — `Choice.value` and the narration — are on no event. `_clean`'s scalars-only rule is a second line of defence rather than the only one, which is the right way round.

**Analytics honours deletion, and I proved it rather than reading the FK declarations.** A real family, played through a real adventure, then deleted through `DELETE /api/v1/account`: **14 events → 0, 8 cost spans → 2**. The two survivors are the ones this report's P1-2 is about, and they are worth being precise about — they carry `child_id=NULL`, `session_key=NULL`, and nothing but a provider name, a unit count and a price. Nothing identifying survives. But they *do* survive, and they did not have to: they are the greeting and companion-asset generations, which run under no cost trace at all even though the `child_id` that would make them cascade is sitting on the row two lines away.

The rest of the day is strong work. §22.3's five metrics are computed from `Event` and from nothing else — I checked, there is no second source — and each definition states the choice it made; `_matured` is the piece that matters most, and it is right (a good week of signups cannot lower day-2 return, because children too new to be judged are out of the denominator, not in it as misses). The cost context genuinely crosses the Celery boundary: `test_the_media_worker_re_enters_the_sitting_context` runs the real task body, through `async_to_sync` and worker threads with their own connections, and asserts no media span lands unattributed. The accessibility pass is real end-to-end, not asserted — I re-measured every contrast pair from `tokens.css` myself and compiled the stylesheet to confirm the 44px target token actually becomes CSS. And the prompt minimisation dropped nothing load-bearing: it touches only schema `description` strings, and the one dropped paragraph that contains a *rule* rather than a rationale ("There are no losing options") turns out to be stated in the instruction layer, where it belongs.

The P1s are all small and all real. Two are gaps between a property the code claims in a comment and the property it has (`track`'s rule 1; the router's early meter read). Two are one-line consistency fixes (the untrimmed default schema; three docstrings pointing at a test file that does not exist). One is the day's own flagged deviation, reassessed and found to be half-defensible (P1-2). One is a full page reload on the first-run call to action, introduced by the polish work itself. The seventh arrived after the fixes were pushed: CI ran at 00:33 UTC, which is the first time this suite has ever run inside the ~4.8-hour window where a fractional-day seed in `test_metrics.py` means "yesterday" instead of "today" (P1-7).

**All seven were fixed in this session** (see *Fixes applied*), re-verified, and pushed.

## Scorecard

| # | Criterion | Verdict | Evidence (cmd/file → result) |
|---|-----------|---------|------------------------------|
| 1 | Events flow from all services; activation / day-2 / W1 / completion / adventures-per-week computable — **show the numbers** | ✅ | Eleven names, a closed set (`Event.NAMES`), emitted from five apps: `accounts/views.py` (signup, consent), `profiles/views.py` (child), `companion/views.py` (companion, onboarding), `story/orchestrator.py` (started, beat, choice, completed, session ended), `safety/incidents.py` (incident). `test_every_name_the_metrics_module_queries_is_in_the_set` closes the loop between writer and reader. **Numbers, from my own seed** — three families over a fortnight, one of which never played, run through `internal.as_text(14)`: activation **66.7% (2/3)**, day-2 return **100% (2/2)**, W1 retention **100% (2/2)**, completion **100% (6/6)**, week of 07-27 **4 adventures / 2 children = 2.00 per child, DWA 0**, week of 08-03 **2 / 2 = 1.00, DWA 0**, 6 sittings, ended reasons `{'completed': 6}`, 0 safety incidents, and a `never emitted:` line naming the four names my seed did not exercise. That last line is the best thing on the page: a metric that has fallen to zero and a service that has stopped emitting look identical everywhere else and different there. |
| 2 | Per-generation cost traced (LLM + image + TTS); **cost/session** on an internal dashboard | ✅ *(with P1-2, P1-5)* | Two seams, and they are the only two that spend money: `ModelRouter.generate_structured` (both the first call **and** the repair — a repaired beat is honestly two `beat` spans) and `media.service._get_or_generate` (the one line in that module that pays a provider; a cache hit writes nothing, which is correct and tested). Units are what the vendor bills: `{input_tokens, output_tokens}`, `{"images": 1}`, `{"characters": len(text)}` — the length only, never the line. `priced=False` rather than a silent zero is the single best decision in `pricing.py`, and `_rate`'s `is None` checks (not an `or` chain) are what keep a genuinely-free route from being misfiled as unknown. `/admin/metrics` is `staff_member_required`, outside `/api/v1/`, and therefore absent from the OpenAPI schema and the generated TS client; `manage.py metrics` renders the *same* `metrics.snapshot` + `cost.cost_per_session`, so the terminal cannot drift from the page. My seeded run: `cost/session $0.0000, total $0.0000 over 36 generations, unattributed $0.0000, unpriced 0` — zero because the scripted provider is priced at zero, which is a known zero and not an absence. The two gaps are **P1-2** (a whole class of spend never enters a trace) and **P1-5** (a metered-then-rejected generation writes no span at all). |
| 3 | Product analytics and the parent-facing subset **separated** | ✅ | Enforced three ways, and I checked each. *Import graph:* `test_no_parent_facing_module_imports_product_analytics` AST-walks six parent-facing modules for imports of `metrics`/`internal`/`cost`/`pricing`, with a companion test that fails if any of those six paths is renamed away (the guard on the guard — that list is exactly the kind of thing that rots into vacuous truth). *On the wire:* five real parent endpoints, played for real, scanned recursively for eleven telemetry field names — and then, crucially, asserted that `Event` and `GenerationCost` rows for that child **do** exist, so the scan cannot pass on a build where the pipeline had simply stopped writing. *Reachability:* the dashboard 302s a parent's token to the admin login. The internal page carries no per-child anything by construction, and `EventAdmin` deliberately omits `search_fields` on a child's name and lists `child_id` rather than `child` so the admin cannot print a four-year-old's first name in every row. |
| 4 | Accessibility: captions, reduced-motion, high-contrast, dyslexia font, adjustable audio work; parent UI meets WCAG AA basics | ✅ *(with P1-6 on the polish half)* | **Re-measured independently**, parsing `tokens.css` myself rather than reading the day's assertions: on `--bg #f7f8fa` / `--surface #ffffff`, `--text` 11.75/12.49, `--text-muted` 4.68/4.97, `--primary` 6.83/7.25, **`--accent` 5.05/5.37**, **`--success` 5.00/5.31** — all clear 4.5:1, and the two the build fixed are exactly where it says they are. Button fill `--surface` on `--primary` 7.25:1; focus ring `--primary` on `--bg` 6.83:1 against WCAG 1.4.11's 3:1. High contrast clears **AAA** in both light (21.00 / 15.06 / 17.57 / 8.96) and night (21.00 / 16.28 / 12.40 / 14.01). The parent surface is `data-theme="calm"` only (`DashboardShell.tsx:68`, `ui.tsx:166`), so calm is the right and only theme to measure. **Targets are real, not asserted**: `npm run build` → the compiled stylesheet contains `.min-h-\[--target-min\]{min-height:var(--target-min)}`, so Tailwind 3.4 does honour the CSS-variable shorthand and the 44px floor genuinely lands. Focus rings are `focus-visible` (never `focus`) and live in one constant that no page can opt out of; the skip link is translated off-screen rather than `hidden`, so it stays in the tab order, and `main` carries `tabIndex={-1}` so focus actually moves — the omission that makes most skip links do nothing. `Section` announces all three states via `aria-live`/`aria-busy`. `Field` puts the hint in `aria-describedby` rather than inside the `<label>`, which is the difference between "labelled" and "usable". |
| 5 | Onboarding/first-run polished | ✅ → ✅ *(after fix)* | The step indicator is one `aria-label` on the `<ol>` with four `aria-hidden` dots, plus the same sentence in text below — colour is never the sole signal, and a screen reader gets one sentence instead of four unlabelled shapes. The first-run empty state is the one that gets more than a line, and the reasoning for that asymmetry is written down: a parent with no children is at a dead end, a parent with no report yet is not. Four other empty states gained real sentences. **The one rough edge is the call to action itself** — `LinkButton` renders a bare `<a href>`, so the primary first-run button does a full document reload inside a SPA (**P1-6**). |
| 6 | `make lint && make test` + CI green | ✅ | `make lint` → ruff check clean, `ruff format --check` clean (**215 files**), eslint clean, prettier clean. `pytest -q` → **exit 0, 1231 tests**. `npm run test` → **170 passed, 12 files**. Both match the commit message exactly. **CI `31627539893` green** on `b91acaf`. |

Counts (as found, before fixes): ✅ 6 · 🟡 0 · ❌ 0 — with six P1s recorded against otherwise-passing criteria.

## The two flagged deviations, assessed

**1. `create_adventure` now creates the `PlaySession` before generating the plan** — *accepted, recorded as P2-3.* The ordering change is necessary and correctly argued: a plan is a full LLM call, §22.4 measures per session, and a charge raised before its sitting existed would be a charge with nowhere to file it. I traced what an orphaned empty row can actually cost us, and it is less than the note feared. Metrics read `Event` and never `PlaySession`, so no §22.3 number moves. `cost_per_session`'s denominator is distinct `session_key` **on spans**, so a sitting that generated nothing is not in it. §13.6's cap groups sittings within `SITTING_GAP_MIN`, so an immediate retry joins the same sitting rather than double-counting. What remains is a row in `story_playsession` with no beats and no `ended_reason`, on a path that was already returning a 500. Acceptable as judged.

**2. Greeting and companion assets are traced but unattributed** — *half-accepted; the other half is **P1-2**.* Leaving them unattributed to a **session** is right, and the note's framing is the correct one: cost/session answers "what does a story cost", not "what does a relationship cost", and inventing a sitting for a generation that happened before any sitting existed would be worse than either. But they are not attributed to a **child** either, and that is not a considered trade — `cost.trace` is simply never opened on those paths, so `child_id` lands null by omission. Two consequences follow, and the second is the one that matters: §22.4 cannot answer "what does onboarding a child cost" even in principle, and those rows are the only analytics rows in the product that survive a family's deletion. See P1-2.

## Punch-list

### P0 — none

The two automatic-P0 conditions in the brief were checked directly and neither fires. No child data in telemetry (every emission site read; `props` is shape throughout). No analytics row carrying child data survives deletion (proven below; the two rows that survive carry `child_id=NULL`, `session_key=NULL` and nothing identifying).

### P1-1 — `events.track` can raise into a caller mid-story

`events.py:130-131` derives the parent **outside** the `try` that exists to guarantee rule 1:

```python
if parent is None and child is not None:
    parent = getattr(child, "parent", None)   # ← one lazy FK fetch, unprotected

try:
    return _write(...)
except Exception:
    logger.exception(...)
```

`child.parent` is a deferred database read on any `Child` not loaded with `select_related`. `getattr`'s default swallows the `AttributeError` that a null FK raises and nothing else — an `OperationalError` or `InterfaceError` from the fetch propagates straight into the caller. That caller is `record_choice` or `_persist_beat`, with a child mid-story on the other end, which is precisely the scenario the module docstring's rule 1 is written about ("the analytics table was locked" must not become a blank screen where a beat was).

**Proven.** A `child` whose `parent` descriptor raises `RuntimeError("database is down")`:

```
tests/test_zz_audit.py:112: in test_audit_track_survives_a_broken_child_parent_fetch
    result = events.track(Event.CHILD_CREATED, child=Exploding())
apps/analytics/events.py:131: in track
    parent = getattr(child, "parent", None)
E   RuntimeError: database is down
```

The exception is the probe's, but the line it escapes from is the product's, and a `DatabaseError` reaches the caller by exactly the same route.

**Fix:** move the derivation inside the `try`.

### P1-2 — the most expensive per-child generation opens no cost trace, and its rows outlive the family

`cost.trace` is opened in five places: `create_adventure`, `create_beat`, and the three Celery re-entries (`story.tasks.generate_beat_media`, `memory.tasks.consolidate`, `analytics.tasks.generate_report`). It is opened in **none** of the companion paths:

```
apps/companion/tasks.py     cost.trace present: False
apps/companion/views.py     cost.trace present: False
apps/companion/service.py   cost.trace present: False
apps/companion/greeting.py  cost.trace present: False
apps/companion/descriptor.py cost.trace present: False
```

Between them these are, per child: a descriptor LLM call, **six illustrations**, a voice line (`generate_companion_assets`), plus the arrival greeting's LLM call, its TTS, and the two safety classifier calls guarding it (`GreetingView`). That is the single largest generation event in a child's life with the product, and every span of it lands with `child_id=NULL, session_key=NULL`.

Two consequences. The unit-economics one: §22.4's "what does it cost to bring a child on board" is unanswerable, and the number is not merely absent from cost/session — it cannot be recovered from the table at all, because nothing on the row says which child it was for. The deletion one is why this is a P1 rather than a P2:

```
BEFORE events=14 costs=8
AFTER  events=0  costs=2
  COST SURVIVED: {'kind': 'image', 'operation': 'illustration', 'provider': 'cloudflare', 'child_id': None, 'session_key': None, ...}
  COST SURVIVED: {'kind': 'llm',   'operation': 'greeting',     'provider': 'scripted',   'child_id': None, 'session_key': None, ...}
```

(A real family: signup → consent → child → companion → a full played adventure → `DELETE /api/v1/account`.) Nothing identifying survives, so the privacy disclosure is not breached. But the `GenerationCost.child` cascade is the mechanism that makes "the data goes at once" true for this table, and a span with no child never enters it. The `child_id` is available at both sites — `companion.child_id` on the row the task already fetches `select_related`, and `child` in the view's own scope.

**Fix:** `cost.trace_ids(child_id=companion.child_id)` around `build_assets`, `cost.trace(child=child)` around `greeting.build`. Session stays null, deliberately and for the reason the build gives.

### P1-3 — the default provider schema skips `trim_descriptions`

`schemas.json_schema_for`'s docstring says the trimming is "applied in `json_schema_for`, which every schema we send passes through, so a new contract gets the same treatment without anyone remembering it". The router does not pass through it:

```python
# router.py:663
json_schema = schema if schema is not None else strip_unsupported_keywords(model.model_json_schema())
```

Callers that pass `schema=` explicitly are fine (`SafetyVerdict`, `SessionMemories`, `SessionHighlight`, and the beat, via `beat_json_schema()`). Three call the router without one — `orchestrator.py:571` (`StoryPlan`), `greeting.py:269` (`ReturningGreetingText`), `descriptor.py:135` (`VisualDescriptor`) — and those three send Pydantic's untrimmed docstrings to the provider's structured-output API. That is the §19.1 layering problem the build itself identified, still live on three contracts, plus the tokens.

**Fix:** one line — `json_schema_for(model)`.

### P1-4 — three docstrings cite a test file that does not exist

`models.py:15`, `metrics.py:35` and `internal.py:14` all point at `tests/test_boundary.py` as the thing that asserts §5.6's import-graph boundary. There is no such file anywhere in the repo (`find . -name "test_boundary*"` → nothing). The assertions are real and good, and they live in `tests/test_analytics_pipeline.py::TestTheSeparationIsStructural`. In a codebase where the docstring is the map — and where `models.py` says in the same breath that "a boundary that is only a docstring is a boundary" — a reviewer who follows the pointer concludes the opposite of the truth.

**Fix:** name the file that exists.

### P1-5 — a metered generation that then fails writes no cost span

Both providers read the token meter early and say why:

> *"Read the meter before anything below can raise. A refusal and a truncated response both cost input tokens, and a span we only write on the happy path would under-report exactly the generations worth knowing the price of (§22.4)."*

The meter is read early. Nothing then writes it: `_trace_llm_cost` is called by the **router**, after `provider.generate_json(...)` has returned, so any raise between the meter read and the return — Anthropic's `stop_reason == "refusal"`, an empty `choices` list, a truncated response, a parse failure inside the provider — discards `last_usage` and the billed input tokens with it. The property is stated in the code and not held by it, and the spend it loses is exactly the spend the comment argues is most worth seeing.

**Fix:** on the failure path, write the span when the provider actually reported a meter reading, then re-raise. Keyed on `last_usage is not None` rather than unconditionally, because a transport failure that never reached the model was never billed and inventing an estimated span for it would over-report — the opposite error, and just as dishonest.

### P1-6 — the first-run call to action does a full page reload

`ui.tsx:267` `LinkButton` renders a plain `<a href>`. Its one caller is `Home.tsx:47` — `<LinkButton href="/parent/onboarding">Add your first child</LinkButton>` — the primary button on the first-run empty state, i.e. the exact screen criterion 5 is about. Inside a React Router SPA that is a full document navigation: white flash, app remount, query cache thrown away. The component's stated reason to exist is sound (`<Link><button>` nests two interactive elements, WCAG 4.1.2), and it is fully satisfied by a router `Link`, which renders one `<a>` with one role and keeps client-side navigation.

**Fix:** render `Link` with the same classes.

### P1-7 — a Day-14 metrics test fails for ~4.8 hours out of every 24

*Found by CI on the fix commit, not by any local run — which is itself the finding.*

`test_metrics.py::TestDay2Return::test_a_child_too_new_to_judge_is_left_out_of_the_denominator` seeds the "too new to judge" child with `_seed(nia, completed_on=[0.2])` — a fractional-day offset, commented "first story a few hours ago". A fractional offset only lands on **today** if the suite runs late enough in the day. Run at 00:33 UTC, 4.8 hours back is *yesterday*, which is old enough to judge, so Nia enters the denominator as a miss and the assertion reads `(1, 2)` instead of `(1, 1)`.

Confirmed both ways at the same clock hour, which is what makes it a diagnosis rather than a guess:

```
$ date -u                       # Thu Aug 13 00:39:05 UTC 2026 — inside the window
$ pytest ...::test_a_child_too_new_to_judge_is_left_out_of_the_denominator   # pre-fix
E   assert (1, 2) == (1, 1)
$ pytest apps/analytics/tests/test_metrics.py                                # post-fix
24 passed
```

Nothing in the fix pass caused it: Day 14's own CI run happened to fall outside the window, as did both of my full local runs. The test two functions below it already anchors to local midnight and says in a comment that it does so precisely to be independent of the hour the suite runs at; this one did not get the same treatment.

**Fix:** anchor the first story to `max(local midnight, NOW − 4h)` — always today, always in the past, at every hour. The other seeds in the file use whole-day offsets, which shift the calendar date by exactly that many days whatever the time, so this was the only one exposed. (`0.2` was the only fractional offset in the suite.)

### P2 — recorded, not fixed

* **P2-1 — `chosen_key` is client text and reaches the export.** `ChoiceRequestSerializer.choiceKey` is a `CharField(max_length=64)` accepted as sent (deliberately: §8.5 means there is no wrong answer to validate against), and `record_choice` puts it verbatim into `Event.props.chosen_key`. Our own client only ever sends an option key, `'count'` or `'continue'` — I traced `StoryPage.tsx`'s three submit paths, and the child's counted number goes in `value`, which is *not* tracked — so nothing leaks today, and the comment claiming `chosen_key` is "a positional key rather than a label" is true of the shipped client. It is not true of the contract. The events table is the one place with an explicit §13.8 minimisation promise and a warehouse export that no delete can reach, so it is worth bounding the value on the event (whitelist against `beat.options` keys plus the two literals) rather than trusting every future client.
* **P2-2 — an empty `units` dict prices as one image.** `cost.price` does `int(units.get("images") or 1)`, and `get_or_generate_stock` deliberately passes no `cost_units` — its comment says "provider `stock`, zero units". Harmless today because `IMAGE_PRICES["stock"]` is `0.00`, but the comment and the arithmetic disagree, and the day someone gives stock a non-zero rate the safety-substitution path starts billing as a generated picture.
* **P2-3 — the empty `PlaySession` row.** See *deviations* above; consequences traced and bounded.
* **P2-4 — per-child fan-out in `metrics.py`.** Flagged by the build itself, correct at alpha scale, and the module says where to look first when it stops being.

## What I tried to break → outcome

| What I tried | Outcome |
|---|---|
| Find any child content in an event's `props` | **None.** All eleven sites read; shape only — age band, list *lengths*, world keys, objective codes, positional keys, classifier labels |
| Get a beat's narration or a child's spoken answer into telemetry | **Not reachable.** `Choice.value` is on no event; `_clean` drops containers and truncates strings to 120 chars as a second line |
| Delete a family and find an analytics row that mentions them | **None found.** 14 events → 0, 8 spans → 2, both survivors `child_id=NULL, session_key=NULL` (**P1-2**) |
| Make telemetry take a beat down with it | **Held for the write** (savepoint isolates the insert; `test_a_failed_event_does_not_poison_the_caller_transaction` proves the caller's transaction survives) — **and failed one line earlier** (**P1-1**) |
| Lose media cost across the Celery boundary | **Attributed.** The real task body, through `async_to_sync` and worker threads: no media span with a null `session_key` |
| Reach `/admin/metrics` with a parent's token | **302 to the admin login.** Not under `/api/v1/`, absent from the OpenAPI schema, absent from the generated TS client |
| Find a second source for a §22.3 metric | **None.** Every query in `metrics.py` reads `Event`; no `Adventure`/`PlaySession` count anywhere in it |
| Make a good week of signups lower day-2 return | **Cannot.** `_matured` excludes children too new to be judged from the denominator rather than counting them as misses |
| Read `--accent` / `--success` as still failing AA | **Both pass** — 5.05:1 and 5.00:1, re-measured from `tokens.css` by an independent parser |
| Catch the 44px target as a class Tailwind never compiled | **It compiles.** `.min-h-\[--target-min\]{min-height:var(--target-min)}` is in the built stylesheet |
| Find a rule the prompt trimming dropped | **None load-bearing.** Only two descriptions lose a paragraph, both rationale; the one rule inside them ("no losing options") is stated in the instruction layer at `ai/prompts/story.py:191` |
| Find a safety rule, memory label or learning tag lost from the beat prompt | **None.** `assembler.py`'s only change is one hunk in `_output_contract` (whitespace); every prose layer is byte-identical |

## Fixes applied

All seven P1s. Six in one pass; P1-7 in a second, after CI on the first fix commit turned it up. No P2 was touched, and nothing the audit marked green was edited.

| # | Fix | Files | Re-verified by |
|---|-----|-------|----------------|
| P1-1 | The parent derivation moved **inside** `track`'s `try`, so every line that can touch a database is under rule 1 rather than only the obvious one. | `analytics/events.py` | `test_a_failing_parent_lookup_is_swallowed_too` — the same stand-in that escaped in Phase 1 now returns `None`; plus `test_a_parent_passed_in_is_not_re_derived`, so the guard cannot have quietly turned the derivation into a no-op |
| P1-2 | `cost.trace_ids(child_id=companion.child_id)` around `build_assets`; `cost.trace(child=child)` around `greeting.build`. **Child only — session stays null**, which is the part of the build's judgement that was right. | `companion/tasks.py`, `companion/views.py` | `test_the_companion_build_is_attributed_to_the_child` (no span with a null child; no span with a session), `test_the_arrival_greeting_is_attributed_to_the_child` (seeded with a memory, because the *first*-meeting hello is pre-vetted and spends nothing), `test_the_view_opens_that_trace`, and **`test_a_deleted_family_leaves_no_cost_span_at_all`** — the Phase-1 probe, promoted to a permanent test: greeting + assets + a played adventure, then `child.delete()`, then `GenerationCost.objects.count() == 0` |
| P1-3 | `json_schema_for(model)` on the router's default branch. | `story/ai/router.py` | `test_a_caller_that_brings_no_schema_still_gets_a_trimmed_one` — asserts the schema the provider was handed is exactly `json_schema_for(StoryPlan)`, and separately that no surviving description contains a paragraph break, so the test fails if trimming is called and stops working |
| P1-4 | The three docstrings now name `tests/test_analytics_pipeline.py::TestTheSeparationIsStructural`. | `analytics/models.py`, `metrics.py`, `internal.py` | Read; the class exists and is the one that asserts it |
| P1-5 | `_generate_traced` wraps `generate_json` and writes the span on the failure path **when the provider actually metered something**. Both call sites (first attempt and repair) go through it, which also removed the duplicated tracing block. | `story/ai/router.py` | `test_a_metered_generation_that_then_fails_is_still_costed` (a refusal after a 900-token read → one `beat` span with those units) and `test_a_call_that_never_reached_the_model_is_not_costed` (no meter → no span, so the fix cannot over-report a call that was never billed) |
| P1-6 | `LinkButton` renders a router `Link`; `Home.tsx` passes `to` rather than `href`. One `<a>`, one role — the WCAG 4.1.2 reason the component exists is untouched. | `parent/ui.tsx`, `parent/Home.tsx` | `routes the first-run call to action without reloading the app` — an `<a>` with no nested `<button>`, the right `href`, and both the target floor and the focus ring still on it |
| P1-7 | The "too new to judge" child is anchored to `max(local midnight, NOW − 4h)` instead of a fractional-day offset, matching what the calendar test two functions below already does. | `analytics/tests/test_metrics.py` | The whole file, green, run at **00:39 UTC** — inside the window that had just failed in CI, and with the pre-fix version failing at the same clock hour two minutes earlier |

**Re-verification, in full.** `make lint` → ruff check clean, `ruff format --check` clean (215 files), eslint clean, prettier clean. `make typecheck` → **mypy: 0 issues in 243 source files**, `tsc --noEmit` clean. `make schema` → `frontend/openapi.json` **byte-identical** (`git diff --stat` empty), as expected: none of the six touches a serializer or a route. Backend `pytest -q` → **exit 0, 1240 tests** (1231 + 9 new). Frontend `vitest run` → **171 passed, 12 files** (170 + 1 new).

**CI.** Run **`31654512385`** on the first fix commit `75bc533` — **frontend green, backend red**, on P1-7 and nothing else (`1 failed, 1239 passed`; the failure is the metrics test above, not one of the six fixes). Run **`31655842931`** on `12f93a2` — **green**, all three jobs, `1240 passed in 382.30s`, matching the local run exactly. That run executed at 00:53–00:59 UTC, i.e. **inside** the window P1-7 fails in, which is what makes it evidence rather than another lucky hour.

**Re-measured after the fix, not assumed:** the deletion probe that produced Phase 1's two survivors now reports **0 events, 0 cost spans**, as a permanent test rather than a scratch file.

## Verdict

**READY.** Day 14 delivers what it set out to: the loop is measurable from one source, spend is visible per generation with an honest unpriced column, the two halves of §5.6 are kept apart structurally rather than by convention, and the accessibility pass is the real thing rather than a checklist. The six P1s are all narrow, all evidenced, and none of them changes the shape of the day's architecture.
