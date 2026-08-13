# Runbook — the eval gate, and how to prove it bites

**What it is.** `evals/` is a CI-gating suite (PRODUCT.md §19.7): golden
scenarios, red-team cases, an age-appropriateness rubric, and regressions
distilled from every `Docs/Review/reports/Day*-report.md`. It runs as the **Evals**
job in `ci.yml`, and `deploy.yml`'s `release` job cannot start until it is green.

**The one sentence to remember.** A change that regresses safety or coherence
beyond the thresholds in `evals/thresholds.py` cannot deploy.

---

## Running it

```bash
pytest evals/ -q                      # the gate. No keys, no spend, deterministic
pytest evals/ -q --live               # the same scenarios against the real provider
pytest evals/ -q --spotcheck          # also writes evals/spotcheck/*.md for a human
make evals                            # the first of these, from the repo root
```

`--live` is the end-to-end red-team pass a person runs before a release. It costs
money and it is **not** the gate — see `evals/conftest.py` for why a gate that
depends on a vendor's afternoon is a gate somebody eventually disables.

---

## The drill: force a fail, watch the deploy block

Run this after any change to the eval harness, the graders, the thresholds or the
CI workflows. It takes about four minutes and it is the only thing that proves
the gate is wired rather than merely present.

### 1. Locally

```bash
PRIMER_EVAL_FORCE_REGRESSION=cliffhanger_close pytest evals/ -q
```

Expected: **red**, on `test_the_sitting_closes_warm`, with the reason printed:

```
golden.warm_close: 0/1 = 0%, below the 100% gate.
    - the close ends unresolved ('nobody knew')
```

The available regressions are `harness.REGRESSIONS`: `ignores_objective`,
`renames_companion`, `cliffhanger_close`, `ignores_choice`, `too_wordy`,
`unsafe_narration`. Each is a real thing a prompt or model change could do, and
each has a grader that catches it.

Then confirm it goes green again with the variable unset. A drill that only ever
shows red proves the suite is broken, not that the gate works.

### 2. In CI, on a throwaway branch

```bash
git switch -c eval-gate-drill
# add the env var to the Evals job in .github/workflows/ci.yml:
#   PRIMER_EVAL_FORCE_REGRESSION: cliffhanger_close
git commit -am "Drill: force an eval regression"
git push -u origin eval-gate-drill
gh run watch
```

Expected: the **Evals** check is red; **Backend**, **Frontend** and **Backend
image** are green — which is the point of it being a separate job, because a red
eval and a red unit test mean different things and are fixed by different people.

Then delete the branch. Never merge it.

### 3. That the deploy is actually blocked

`deploy.yml` runs on a `v*` tag and its `release` job declares
`needs: [gate]`, where `gate` calls `ci.yml` in full. GitHub will not start a job
whose `needs` has failed, so a red eval on the tagged commit means `release`
never runs — it is reported as *skipped*, not as passed.

To see it rather than believe it, tag the drill branch (`git tag v0.0.0-drill &&
git push origin v0.0.0-drill`), watch the Deploy workflow, and confirm `release`
is skipped with `gate` red. Delete the tag afterwards
(`git push --delete origin v0.0.0-drill`).

`evals/test_gate.py::test_the_deploy_workflow_actually_requires_this_suite`
asserts each link of that chain on every commit, so the wiring cannot be removed
by a CI refactor without a test going red.

---

## Drill log

Every time the drill is run, add a line. An entry older than the last change to
`evals/` or to the workflows means the gate is unproven.

### Local (done — Day 15 build session, 2026-08-13)

Every forceable regression was run, and each turned the suite red on the grader
that exists to catch it. The control run — nothing forced — is green.

| Forced | Suite | Failed on |
|---|---|---|
| _(nothing — control)_ | ✅ green | — |
| `cliffhanger_close` | ❌ red | `test_the_sitting_closes_warm` → `golden.warm_close: 0/1 = 0%, below the 100% gate. — the close ends unresolved ('nobody knew')` |
| `renames_companion` | ❌ red | `test_the_companion_is_one_character_throughout` |
| `ignores_objective` | ❌ red | `test_the_selected_objective_is_actually_exercised` (+ coherence) |
| `too_wordy` | ❌ red | `test_every_beat_a_child_hears_fits_their_age` |
| `unsafe_narration` | ❌ red | coherence, objective, and the graders'-own-bite tests |

### In CI — control row done, **red row still owed**

| Date | Ref | What was forced | Evals | Other CI jobs | Deploy |
|---|---|---|---|---|---|
| 2026-08-13 | `main` @ `6b23b57` | nothing (control) | ✅ green, run **`31665610393`** — `41 passed, 1 skipped` in 18s, matching the local run exactly | ✅ Backend, Frontend and Backend image all green | n/a — no tag |
| _(owed)_ | `eval-gate-drill` | e.g. `cliffhanger_close` | expect ❌ | expect ✅ | expect `release` never starts |

> The run ids in this table are the evidence, not decoration. Two rows make a
> complete drill — the same suite, red for a real reason and green without it —
> and **only the green one is filled in**. A green control alone proves the job
> runs; it does not prove the pipeline stops. Run step 2 above and complete this.
>
> Until then the standing guard is
> `evals/test_gate.py::test_the_deploy_workflow_actually_requires_this_suite`,
> which asserts every link of the chain (release → gate → ci.yml → the `evals`
> job, and no failure-softening flag in the deploy) on every commit — so the
> wiring cannot be broken silently while this row waits.
>
> `evals/test_gate.py::test_the_deploy_workflow_actually_requires_this_suite`
> asserts the wiring (release → gate → ci.yml → the `evals` job, and no
> failure-softening flag in the deploy) on every commit, so the chain cannot be
> broken silently while this table waits to be filled in.

---

## Changing a threshold

`evals/thresholds.py` holds every gate, and its docstring states the rule:

* **Safety and regression gates are 1.00 and stay there.** `test_gate.py` asserts
  it, so lowering one puts that test in the diff where a reviewer sees it.
* **Quality gates are judgements** and may move, but *down* only with the failing
  cases in the commit message and an issue linked. Lowering a gate to make a
  build green is how a suite stops meaning anything.
* **Up is free**, once the suite has been above the new number for a week.

## Adding a case

1. Reproduce the problem. Add the input to `apps/safety/redteam/corpus.py` (for
   safety) or a case to `evals/test_regression.py` (for anything a review found),
   **stated as the symptom a person saw** rather than as the mechanism.
2. Watch it fail. A case added after the fix is a case that never proved anything.
3. Fix it. Watch it pass.

`evals/test_regression.py::test_the_regression_suite_covers_every_day_that_had_a_p0`
reads the review reports and fails if a day recorded a P0 with no case naming it,
which is what stops this suite quietly falling behind.
