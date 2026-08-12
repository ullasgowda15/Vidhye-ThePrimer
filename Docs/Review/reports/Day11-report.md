# Day 11 Review Report — Personalization Depth (Every Child a Different World)
**Reviewed:** 2026-08-12 · **Commit:** `8468107` ("Day 11: personalization depth — interest graph, world map, per-world palettes") · **Reviewer:** Claude Code (combined review-then-fix session, both phases authorised by the founder)

## Overall verdict
**FIX-THEN-SHIP** — no P0. The day's headline claim holds and holds *structurally*: two children of the same age, handed the same objective by the same catalog and written by the same scripted provider, get different worlds, different places, different inhabitants, different palettes and two materially different plan requests — and the difference is produced by code a reviewer can read, before a model is involved at all. The interest graph genuinely updates from behaviour, decays on a measured 30-day half-life, and deletes what has faded. The fear ceiling is enforced as a *filter on what is offered* rather than as a sentence in a prompt, on every branch including the revisit branch, which is the strongest form of that guarantee this codebase has yet produced.

Three P1s keep it off SHIP. One is an accessibility regression the day introduced (`--text-muted` drops below WCAG AA on three of the four new palettes, where the base Storyland palette passes). One is a long-run world-map defect: after a child has worn out every place in their world, `select_place` pins them to a single place **forever** — and, because discovery runs in registry order, it pins them to that world's *most intense* place. One is the topic vocabulary: a real played sitting produces `river, frog, sparkling, follow, dragonflie, ask` as the child's top six interests, and half of that is not a list of things anybody loves.

**All three P1s were fixed in this session** (see *Fixes applied*), re-verified, and pushed.

## Scorecard

| # | Criterion | Verdict | Evidence (cmd/file → result) |
|---|-----------|---------|------------------------------|
| 1 | `InterestEdge` weights update from behaviour (chosen topics rise; unrelated decay) | ✅ | Probed against the real `played` fixture (five beats, two branch taps, a count, a wobble, closed through the orchestrator — not hand-built rows). Before: `[]`. After `update_from_session`: `updated reinforced=8 forgotten=0`, weights `river 0.35, follow 0.35, sparkling 0.35, ask 0.35, dragonflie 0.35` (chosen card labels, `CHOSE=0.35`) against `dawn 0.08, mossy 0.08, riverbank 0.08` (scenery the child merely sat through, `ENCOUNTERED=0.08`) — the grading works and stops the graph becoming a graph of our own narration. Decay measured off `current_weight`: a 0.90 edge reads **0.9000 / 0.7656 / 0.4500 / 0.2250 / 0.1125 / 0.0141 / 0.0002** at 0/7/**30**/60/90/180/365 days — exactly `HALF_LIFE_DAYS = 30`, applied at *read* time, so nothing rewrites a row to record that nothing happened. Forgetting verified end-to-end: a 0.60 edge aged one year reads 0.00013 (`FORGET_BELOW=0.05`) and the next `apply_reinforcement` returns `forgotten=1` with the row gone. Saturation is real (`0 < after_last − before_last < first_step/10` over ten reinforcements), so a lifelong favourite and a new obsession stay distinguishable. See **P1-3** for the *vocabulary* the weights are attached to. |
| 2 | Planner prefers high-interest topics + reuses recurring characters/known places | ✅ | Selection is a pure, readable function of the graph: `world_score` sums matching topic weights, `select_world` logs `story.world_selected child=… world=coral_deep score=0.80 interests=sea,whale`. It moves when the child moves — a dinosaur child scores `sunlit_canyon 0.40` from seeds, and after four `SPOKE` reinforcements of "whale" flips to `coral_deep` (`test_the_world_follows_the_interest_graph`). Reuse reaches the prompt, verbatim from the second plan request in my probe: *"Already at this place: the hermit crabs, a tidy old turtle called Marigold, **Mo the octopus**"* and *"Characters this child already knows: **Mo the octopus**. Bringing one of them back is better than inventing someone new."* The rest of the map arrives as `KNOWN_PLACES_RULE`, deliberately framed as somewhere to *point toward* rather than as an alternative setting — the right call, since the ceiling was applied to the place that was actually chosen. Cast is filtered to `.exclude(visual_descriptor="")`, so a name the planner uses is always a creature the pipeline can draw again (Day 6). |
| 3 | **Two profiles → visibly different worlds/stories** for the same objective, structurally | ✅ *(with a stated limit)* | Full side-by-side below. Same age, same `count_to_3` at difficulty 0.10, same scripted writer → **different world** (`sunlit_canyon` / `coral_deep`), **different place** (the Big Bone Steps / the Shell Garden), **different mood** (`warm_and_wide` / `cool_and_shimmering`), **disjoint inhabitants**, **different palette key on the wire**, and two plan requests that differ in four paragraphs, not in a noun. `test_two_profiles_produce_structurally_different_worlds` asserts each of those. **The limit, stated plainly:** the *narrative* difference is unproven — `ScriptedAIProvider` returns one fixed plan regardless of input, so both children's `Adventure.plan` carries the identical title and skeleton. What is proven is that the two models were asked two different questions; what a real model does with them is the founder manual test. Same posture as Day 10's report on real-model verification. |
| 4 | **≥3 distinct world palettes** render matching mood; night-friendly variant works | 🟡 → ✅ *(after fix)* | Four worlds, four day palettes, four night palettes, all in `tokens.css`, applied as `data-world` on `document.documentElement` by `useWorldTheme`. `worlds.test.ts` reads the stylesheet **as bytes** and asserts each block re-points `--bg/--surface/--primary/--accent/--text/--border` and that `--bg/--primary/--accent/--text` are unique across all four — so "a world that silently renders as the default" is a test failure. Night is a *variant* of each world (specificity `[data-theme='night'][data-world='…']` beats the day block), asserted to differ in both `--bg` and `--text`. Moods match: woods green, harbour indigo, coral teal, canyon ochre. **What failed:** §15.1's own rule — "WCAG AA contrast minimum". Measured `--text-muted` on `--bg`: **coral_deep 3.87:1, star_harbor 4.20:1, whispering_woods 4.48:1** against base Storyland's 5.13:1, at 14–18px in the StoryPage footer, `SceneTransition`'s waiting line and ChildHome. That is **P1-1**, fixed below. Night palettes are comfortably clear (7.0–7.6:1). |
| 5 | Recurring character/place persists + reappears **consistently** across two adventures | ✅ | Probed across two real adventures. Adventure 1 discovers `the Shell Garden`; a beat introduces Mo; adventure 2 returns to the **same row** (`place_id` identical, `visit_count=2`, `returning=True`), the inhabitants list has grown to `['the hermit crabs', 'a tidy old turtle called Marigold', 'Mo the octopus']`, and Mo's `visual_descriptor` is **byte-identical** across both — Day 6's consistency untouched. The second plan request carries `RETURNING_RULE` and only the second (`test_the_same_place_is_returned_to_and_is_the_same_place` asserts exactly one). The design decision underneath is the right one and worth naming: `record_visit` uses the registry text **only to create** the row; after that the row is what the place is, so editing the registry between releases cannot repaint a place a child already knows. |
| 6 | Fear ceiling respected in personalised content | ✅ | Enforced as a filter, at four points, and I checked each: `select_world` drops any world with nowhere reachable (`_reachable`); `select_place` filters the **revisit** branch by `_intensity_of(row) <= ceiling` as well as the discovery branch; `_elsewhere` leaves the world entirely rather than relaxing the ceiling; and the fallback plan path still carries the ceiling-filtered setting. Exhaustive sweep on a three-year-old at `intensity_ceiling=0`, every world, four visits each: **max intensity offered = 0** in all four worlds; the eight places offered are all intensity-0. `test_a_lowered_ceiling_takes_a_place_back` proves the sharp case — a parent lowering the ceiling takes back a place the child has *already been to*, not merely the next one. And personalisation cannot widen the policy: the system layer on the personalised plan call still reads `Intensity ceiling: 2 of 3 … Never exceed it. When unsure whether a moment is too much, go gentler.` at the value the parent set (`test_personalisation_cannot_widen_the_safety_policy`). Interest topics reach the prompt as letters-only single words produced by `topic_tokens`, so the graph is injection-safe by construction; registry names and row-sourced names are additionally run through `safety.scrub_profile_field`. |
| 7 | `make lint && make test` + CI green; no regression to Day 6 / Day 7 / Day 9 | ✅ | `make lint` → ruff check + `ruff format --check` (**180 files**) + eslint + prettier, all pass. `make test` → backend **943 passed** in 420s, frontend **108 passed** (9 files), exit 0. `mypy apps/memory/interest.py apps/story/worlds.py apps/story/orchestrator.py` → clean. `manage.py makemigrations --check --dry-run` → "No changes detected". `spectacular` re-export → `frontend/openapi.json` byte-identical to the commit; `npm run client` → `schema.ts` byte-identical; `npm run typecheck` → clean. **CI green on `8468107`** (run `31532234119`, conclusion `success`). No regression found in the three named areas: Day 6 consistency is untouched (character descriptors stable across adventures, `_known_characters` unchanged for beats, places deliberately kept out of the per-beat prompt); Day 7 safety is intact (assembler still reads the profile, red-team suite green inside the 943); Day 9 memory is intact (`plan_context` gained the *place* as a retrieval term — an addition, and the highest-value one on a revisit — and retrieval still logs `considered=3 kept=3 … tokens=46/800 degraded=0`). |

Counts (as found, before fixes): ✅ 6 · 🟡 1 · ❌ 0

## Criterion 3's evidence: two children, one objective, side by side

Both five years old. Both selected `count_to_3` at difficulty 0.10. Both written by the same `ScriptedAIProvider`.

| | **Rex** — loves dinosaurs, digging | **Nia** — loves the sea, whales |
|---|---|---|
| interest ranking | `digging, dinosaur` | `sea, whale` |
| world (score) | `sunlit_canyon` (0.40) | `coral_deep` (0.80) |
| place | the Big Bone Steps | the Shell Garden |
| mood | `warm_and_wide` | `cool_and_shimmering` |
| inhabitants | the little striped lizards; a long-necked dinosaur called Juniper | the hermit crabs; a tidy old turtle called Marigold |
| palette on the wire | `world.key = "sunlit_canyon"` → ochre/`#fbf0e2` | `world.key = "coral_deep"` → teal/`#e8f4f3` |
| objective | `count_to_3` @ 0.10 | `count_to_3` @ 0.10 |

The two plan requests, at the paragraphs that differ (the first five paragraphs are identical base ask):

> **Rex:** *"This adventure happens in **the Sunlit Canyon**, at **the Big Bone Steps** — a wide sandstone shelf where enormous old bones lie in the rock like steps, warm to sit on, with the whole canyon golden below. The mood of the place is **warm and wide**. Set the whole story here, and let the place do real work in the plot rather than being scenery behind it."*
> *"Already at this place: **the little striped lizards, a long-necked dinosaur called Juniper**."*
> *"This child is currently in love with: **digging, dinosaur**. Build the quest out of the first one or two of those — what the problem is, who needs help, what the child ends up doing — rather than mentioning them in passing."*

> **Nia:** *"This adventure happens in **the Coral Deep**, at **the Shell Garden** — a warm shallow lagoon where the sand is white and every shell is a different colour, laid out in rows by somebody very tidy. The mood of the place is **cool and shimmering**. …"*
> *"Already at this place: **the hermit crabs, a tidy old turtle called Marigold**."*
> *"This child is currently in love with: **sea, whale**. …"*

This is the shape of the claim I can verify without a provider: the personalisation layer produces two different *questions*, deterministically, from two profiles. It is a stronger form of evidence than two nice-looking stories would be, because the branch that was taken is readable. It is also **not** evidence that the two finished stories read differently — that needs a real model and belongs to the founder manual test.

## The flagged signature deviation — `build_plan(child, selection, personalization)`

**Assessment: accept.** The brief's §5 stub is `build_plan(child, top_interests, known_places, recurring_characters, fear_ceiling, objective, difficulty)`; the build shipped `build_plan(child, selection=None, personalization=None)`, where `Personalization` is a frozen dataclass carrying exactly `top_interests`, `known_places`, `recurring_characters`, `fear_ceiling` — plus `setting`, the world/place chosen from them — and `learning.Selection` carries `objective` with `difficulty`.

Three reasons this is the better shape and not a shortcut:

1. **Every named input is present and reaches the model.** Nothing was dropped in the bundling; the plan request contains all four (interests, inhabitants of the chosen place, the rest of the map, the known cast), and the ceiling is enforced upstream of the call rather than restated in it.
2. **Splitting `objective` from `difficulty` is a known hazard in this codebase.** Day 10's `Selection` exists precisely so a beat cannot practise one thing at another thing's difficulty; the brief's stub would have re-separated them one layer up.
3. **§5 is "Key stubs", not architecture.** Per CLAUDE.md the brief wins for scope-of-the-day and PRODUCT.md wins for architecture — the scope (what the planner must know about the child) is delivered in full, and the shape is a local call-signature decision.

One caveat, recorded as **P2-1**: `Personalization.fear_ceiling` is **set and logged but never read**. The dataclass docstring claims the bundle exists so "a future caller must not be able to build a plan with the interests and without the ceiling", and today that guarantee is not what the field provides — enforcement is `child.intensity_ceiling`, read directly inside `worlds.select_place` and independently by the safety assembler. The behaviour is correct; the docstring overstates the mechanism.

## What's working well

- **The fear ceiling became structural, not advisory.** The registry pins an intensity to every place and `select_place` never *offers* one above the child's. This is the difference between "we asked the model not to go there" and "it was never given the address", and the module says so in as many words. The `_elsewhere` branch is the tell that the thinking went all the way through: when §11.6 and §8.3 disagree, the child leaves the world rather than the ceiling bending.
- **Why the worlds are a shipped registry rather than a model's invention** is argued from three independent directions and each one is real: a palette must be a key the CSS knows (`data-world="coral_deep"`, not "The Glimmering Tidepools"); a place's intensity must be *known before* planning, not judged after; and nothing generated twice is the same place, which is the whole of §8.3. Personalisation lives in the *selection*, which is exactly where it can be tested.
- **Decay as a function, not a job.** `weight` means "this much, as of `last_reinforced_at`", and every read ages it. The alternative — a nightly pass rewriting every edge — would have made the column say "when we last ran the job" instead of "when this child last cared", which is precisely the column Day 12's "this month: space, then dinosaurs, now ocean" view has to read. Measured half-life is exactly 30 days.
- **Three post-session siblings, three separate `try` blocks.** `update_interests` reads no provider at all, which is why it is correctly a sibling of consolidation rather than a step inside it: a graph that stopped updating because an embedding vendor was down would take a fortnight to notice and could never be rebuilt, since §17.5 prunes the beats it reads. Idempotency is gated on `PlaySession.interests_updated_at` and re-checked inside `select_for_update`, so a Celery redelivery cannot invent a favourite.
- **The graph reads persisted rows, not the Redis log.** `Choice`, `Beat` and `PlaySession.stm_digest` all survive a Redis outage. Deliberate, documented, and the right trade.
- **The palette falls back to *nothing* rather than to a default world.** `worldAttribute` returns `undefined` for a key the stylesheet does not know, so an adventure planned before Day 11 renders in the complete base theme. "A wrong palette is worse than no palette: it tells the child they are somewhere they are not" is the correct instinct.

---

## Punch-list

### P0 — blockers
*(None.)*

### P1 — should fix
- **P1-1 — `--text-muted` fails WCAG AA on three of the four new world palettes.** ✅ **FIXED.** §15.1's rules open with "WCAG AA contrast minimum". Measured against each world's own `--bg`:

  | palette | `--text-muted` on `--bg` | verdict |
  |---|---|---|
  | base Storyland (`:root`) | 5.13:1 | pass |
  | `whispering_woods` | **4.48:1** | fail |
  | `star_harbor` | **4.20:1** | fail |
  | `coral_deep` | **3.87:1** | fail |
  | `sunlit_canyon` | 4.63:1 | pass |

  `--text-muted` is rendered at 14–18px (`text-1`/`text-2`/`text-3`) in the StoryPage footer, `SceneTransition`'s waiting line, and ChildHome — normal text, so 4.5:1 is the bar, and `useWorldTheme` puts these tokens on `document.documentElement` for the whole story surface. Every other rendered pair holds: `--text` on `--bg` is 9.4–11.2:1, `--primary` is only ever used at ≥24px (large-text bar 3.0:1) and clears it in all four, and every night palette is 7.0–7.6:1. *(Separately noted, **not** a Day-11 defect: `text-surface` on `bg-accent` — the child's primary buttons — is ~2.1:1 in the **base** Storyland theme and 1.8–2.6:1 across the worlds. That is a pre-existing issue from the original token set, out of this day's scope, and worth its own ticket.)*
  **Fix applied:** darkened `--text-muted` in the three failing world blocks only, keeping the hue: `whispering_woods #66755d → #5a6952` (**5.34:1**), `star_harbor #6a7292 → #5d6584` (**5.08:1**), `coral_deep #5b7f85 → #4c6f75` (**4.86:1**); `sunlit_canyon` was already passing at 4.63:1 and is unchanged. Night palettes untouched (7.0–7.6:1). Added a contrast assertion to `worlds.test.tsx` that computes the WCAG ratio from the stylesheet bytes for `--text` and `--text-muted` in all eight blocks, so a future palette cannot ship below AA — verified to *fail* when `coral_deep`'s old value is put back.

- **P1-2 — Once a world is exhausted the child is pinned to one place forever, and it is the world's most intense place.** ✅ **FIXED.** Traced twelve consecutive adventures for a strongly-personalised ocean child:

  ```
  adv  1–3 : the Shell Garden   (discover, return, return)
  adv  4–6 : the Singing Kelp   (discover, return, return)
  adv  7–9 : the Lantern Reef   (discover, return, return)
  adv 10   : the Lantern Reef
  adv 11   : the Lantern Reef
  adv 12   : the Lantern Reef        … and so on, indefinitely
  ```

  `select_place`'s exhausted branch returns `known[0]`, and `known` is ordered `-last_visited_at` — so the visit it just recorded keeps that place at the head of the list forever. Two consequences. First, §8.3's "repetition-with-variation" collapses into plain repetition and the other two places in the child's own map are never returned to, which is the generic-content failure of §2.4 arriving from the opposite direction. Second, because discovery walks the registry in order and the intensity-1 place is written last, the place the child is pinned to is the **most intense one in the world** — not a ceiling breach (the child's ceiling permits it) but the opposite of the gentle default the rest of this module is built on. The code's own justification — "`select_world`'s novelty tie-break means the next sitting will usually pick a different world anyway" — is only true for a child with a *flat* interest graph; for the strongly-personalised child this day exists to serve, `world_score` wins outright every time (`coral_deep score=0.80` on every one of the twelve).
  **Fix applied:** on the exhausted branch, return `known[-1]` — the least recently visited known place — instead of `known[0]`, so the map rotates through everywhere the child knows. Comfort is preserved (nowhere new is invented, nothing above the ceiling is offered, the row is still the row); what changes is that "somewhere they know" stops meaning "the same one". Regression test added (`test_a_worn_out_world_still_moves_the_child_around_it`).

- **P1-3 — The topic vocabulary admits non-subjects and mangles common plurals.** ✅ **FIXED.** The top six interests after one real played sitting:

  ```
  river 0.750 · frog 0.400 · sparkling 0.350 · follow 0.350 · dragonflie 0.350 · ask 0.350
  ```

  Three of the six are not things a child loves — they are the verbs and adjectives of the choice-card labels the child tapped ("Follow the sparkling river", "Ask the dragonflies") — and one is not a word. `_STOPWORDS` already filters `look/make/take/come` for exactly this reason but stops short of the vocabulary the cards actually use. Separately, the crude singulariser turns any `-ies` plural into `-ie`: **dragonflies→dragonflie, butterflies→butterflie, bunnies→bunnie, puppies→puppie, ponies→ponie, stories→storie** — the same class of failure the module already fixed for `-ss` ("moss" must not become "mos"), and by the module's own stated standard ("nothing a person would recognise in a parent-facing list of what their child loves") these fail it too. The existing test hedges rather than asserts: `assert "dragonflie" in topics or "dragonfly" in topics`. Impact is bounded but real in three places: the `INTEREST_RULE` paragraph literally reads *"This child is currently in love with: … sparkling, follow, … ask"*; `engine.interest_fit`'s tie-break gains spurious match surface; and §11.7 promises this list to parents as "a lovely, true window", which is Day 12's input.
  **Fix applied:** the singulariser moved into a named `_singular` with three stated rules (`-ss` kept, `-ies → -y`, `-ches/-shes/-sses/-xes/-zes` lose both letters — deliberately *not* `-ses`, which would take "horses" to "hors"), and `_STOPWORDS` gained the verb/adjective vocabulary a choice card is written in. The same sitting now produces `river 0.750 · frog 0.400 · dragonfly 0.350 · riverbank 0.080 · mossy 0.080 · dawn 0.080` — six subjects, no verbs, correctly spelled. The hedged assertion is now exact, and two new tests pin the behaviour: one on the plurals a five-year-old actually uses (dragonflies, butterflies, bunnies, puppies, foxes, beaches, horses), one asserting `topic_tokens("Follow the sparkling river") == {"river"}`.

### P2 — improvements (non-blocking, not fixed in this session)
- **P2-1 — `Personalization.fear_ceiling` is decorative.** Set from `child.intensity_ceiling`, logged, never read. The dataclass docstring claims the bundle prevents a caller "building a plan with the interests and without the ceiling"; nothing enforces that, because the ceiling that does the work is re-read inside `worlds.select_place`. Either thread the field through to `select_world`/`select_place` (making the object genuinely authoritative), or amend the docstring to say the field is telemetry. Behaviour is correct either way.
- **P2-2 — `top_interests` is queried twice on the story-start path.** `create_adventure` calls `personalize(child)`, and `learning.select_objective(child)` independently calls `_interest_tokens(child) → selector_tokens → top_interests`. Two full reads of the child's edge table per adventure start, one of them for a documented tie-break. Cheap to pass through.
- **P2-3 — `visit_count` increments at adventure *start*, not completion.** A child who starts and abandons — or a `useResetAdventure` retry — ages a place toward `NOVELTY_AFTER_VISITS` without having played it. Three resets and the Hollow Oak is "worn out".
- **P2-4 — Nothing checks that a generated plan is coherent with the setting it was planned into.** Visible in the fixture: `Adventure.plan` for the dinosaur child holds `title: "The Frog Chorus"`, a riverbank skeleton, **and** `world: sunlit_canyon, place: the Big Bone Steps`. A scripted-provider artefact today, and there is no reason a real model would do it — but `plan.model_dump()` and `setting.as_plan_fields()` are merged with no consistency check, so nothing would notice.
- **P2-5 — Places whose registry entry we later rename are treated as intensity 0.** `_intensity_of` returns 0 for an unrecognised name. Documented and defended (the child has already been there happily), and I agree with the call — recording it so it is a known property rather than a discovery, since it is the one path where a place's intensity is not read from the registry.
- **P2-6 — Working-tree hygiene, unchanged from Day 10's P2-5.** `Docs/Test/` and `temp/` are untracked, and an unrelated change sits uncommitted alongside the day's work: the ElevenLabs default model moved `eleven_multilingual_v2 → eleven_flash_v2_5` in both `.env.example` and `backend/config/settings/base.py`, and `Docs/Manual-Test.md` is deleted. None of it is Day 11 and none of it is committed; it should be landed or reverted deliberately rather than carried into Day 12. *(Also: `Docs/Day11-Build.txt`, present as an untracked file when this session opened, was gone from disk by 07:35 — not removed by this session, which touched only its own temporary probe files. Flagging it rather than acting on it.)*

## Scope & risk notes
**Scope discipline: clean.** No parent-facing surface — `grep` over `views.py`/`serializers.py` finds no interest, world or place endpoint; the only human-readable surface is Django admin. No companion evolution: `evolution_state` is untouched by this commit and the companion stays at stage 0. The day did not exceed the fear ceiling to chase engagement — it did the opposite, making the ceiling a filter on the *offer*. Day 6 consistency, Day 7 safety and Day 9 memory are all intact and independently checked above.

The commit reached beyond `memory/` and `story/worlds.py` — `learning/engine.py`, `story/orchestrator.py` (+264), `story/ai/prompts/story.py` (+142), `serializers.py`, and four frontend files — but each is the wiring this day required (the graph landing in the Day-10 seam, personalisation before the plan, the six new plan rules, the world on the wire, the palette layer), and the full 943-test backend suite plus 108 frontend tests are green.

**Risk carried forward:** none blocking. P2-3 is the one most likely to surface as a puzzling product behaviour ("why did my child leave the Hollow Oak after two real visits?") once the client can retry a start.

## Unverified items (and why)
- **Whether two finished *stories* read differently.** Every plan and beat in this audit was written by `ScriptedAIProvider`, which returns the same skeleton whatever it is asked. No `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY` in this environment, so `make smoke-story` could not run. Structure, selection and prompt are proven; prose is not. **This is the single largest unverified claim of the day** and is exactly what the founder manual test (Day 11 §8) exists to settle — recommend running it against a real provider for both children before Day 12.
- **What the palettes actually look like.** Contrast is arithmetic and I have measured it; "warm, storybook, never overstimulating" is a human judgement, and a screenshot of the Coral Deep at seven in the evening is not something a test can stand in for.
- **Long-run interest-graph behaviour.** Everything here is one or two sittings deep. Nobody has simulated a month of play to see whether the ranking a child actually experiences drifts sensibly or thrashes. P1-2 is the kind of thing that only appears at that horizon, and it appeared at twelve adventures — worth assuming there are others.

---

## Fixes applied (Phase 2 of this session)

Per the founder's instruction, the P1 punch-list was fixed in the same session, after this report was written. Nothing the audit marked ✅ was touched: the fear ceiling, Day-6 consistency and Day-9 memory paths are unchanged.

| Item | Status | Files |
|---|---|---|
| P1-1 — palette contrast below AA | ✅ Fixed | `frontend/src/design/tokens.css`, `frontend/src/test/worlds.test.tsx` |
| P1-2 — exhausted world pins to one place | ✅ Fixed | `backend/apps/story/worlds.py`, `backend/apps/story/tests/test_worlds.py` |
| P1-3 — topic vocabulary noise + mangled plurals | ✅ Fixed | `backend/apps/memory/interest.py`, `backend/apps/memory/tests/test_interest.py` |
| P2-1 … P2-6 | Not fixed — non-blocking, carried forward | — |

**Re-verification after the fixes**, re-measured rather than assumed:

- **Contrast:** `whispering_woods 5.34:1`, `star_harbor 5.08:1`, `coral_deep 4.86:1`, `sunlit_canyon 4.63:1` (unchanged) — all above AA, all still the hue of their world; night palettes untouched at 7.0–7.6:1. The new assertion was proved to *fail* by restoring `coral_deep`'s old `#5b7f85` and watching the suite go red, then reverted.
- **The map now rotates.** Fifteen consecutive adventures for the same strongly-personalised ocean child: `Shell Garden ×3 → Singing Kelp ×3 → Lantern Reef ×3 → Shell Garden → Singing Kelp → Lantern Reef → Shell Garden → Singing Kelp → Lantern Reef`. Discovery order and the three-visit comfort rule are unchanged; only the fixed point is gone.
- **Topics after the fix**, same played sitting: `river 0.750 · frog 0.400 · dragonfly 0.350 · riverbank 0.080 · mossy 0.080 · dawn 0.080` — where it was `river, frog, sparkling, follow, dragonflie, ask`.
- **Suites:** `make lint` clean (ruff check + `ruff format --check` over 180 files, eslint, prettier) · backend `pytest` **945 passed** · frontend `vitest` **109 passed** · `mypy` clean on the touched modules · `makemigrations --check --dry-run` → "No changes detected" · OpenAPI schema and typed client unchanged, since no API surface was touched · CI green on the fix commit.
- **One flaky test found and fixed before commit — mine, not the build's.** The first post-fix suite run came back `1 failed, 944 passed`; the next two were clean. The cause was in P1-2's new regression test rather than in the fix: `record_visit` stamps `timezone.now()`, and this machine's clock resolves to **~1 ms** (2000 consecutive `timezone.now()` calls returned **3** distinct values), so a tight loop of visits ties on `last_visited_at`, the ordering falls through to `name`, and `known[-1]` becomes the alphabetically last place instead of the least recently visited one. Real visits are an adventure apart, so this never reaches production — but a test that passes on clock luck is worse than no test. It now dates each visit a day on from the last, which is what the ordering actually sees in production. Verified stable over eight consecutive runs of the suite.

One deliberate restraint inside P1-3: `sing`, `dance`, `play`, `magic` and `secret` were *not* added to the stopword list even though they are card copy most of the time. A word filtered there can never be reinforced however often a child reaches for it, and the failure is silent — so when a word could be either a description or a genuine love, it stays in. That reasoning is recorded in the module.

### Note on side effects of this review
Four temporary probe files were written under `backend/apps/{story,memory}/tests/` to gather the evidence quoted above (the two-profile comparison, the interest-graph and decay tables, the ceiling sweep, the map trace, the recurrence check, and the post-fix re-measurement). All four were deleted; `git status` confirms the tree holds only the intended fixes plus the pre-existing untracked items recorded in P2-6. The local Docker `db`/`redis` containers were used; pytest runs against its own test database and no application data was modified.

Two things in the working tree were deliberately **left out of the fix commit**, because they are not Day 11: the uncommitted ElevenLabs model-default change (`.env.example`, `backend/config/settings/base.py`) and the deletion of `Docs/Manual-Test.md` alongside the new `Docs/Test/`. They are recorded in P2-6.

**Update, after the report was filed:** at the founder's instruction both were then landed on their own, separately from the fix commit, so P2-6 is now closed except for `temp/`:

- `4da7d09` — *Docs: split the manual test into `Docs/Test/`* (`Manual-Test.md` → `test-prerequisites.md` + `Day10-manual-test.md`; checked first that nothing in the repo referenced the old path).
- `a09b898` — *Config: default ElevenLabs to `eleven_flash_v2_5`* (half the credit cost per character, lowest latency, still overridable).

`temp/` was left untracked at the founder's instruction, as was `Docs/Day11-Review&Fix.txt` (this session's transcript, the same shape as the `Docs/DayN-Build.txt` files, which have never been committed deliberately). Separately: `Docs/Day11-Build.txt`, present as an untracked file when this session opened, was gone from disk by 07:35 — this session touched only its own probe files, so it was removed by something outside it (OneDrive sync is the likely candidate). Flagged, not acted on.
