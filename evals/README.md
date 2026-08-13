# `evals/` — the ship gate

PRODUCT.md §19.7's offline eval suite. It runs as the **Evals** job in CI, and
`deploy.yml`'s `release` job cannot start until it is green.

```bash
make evals                    # the gate. No keys, no spend, deterministic
pytest evals/ -q --live       # the same scenarios against the real provider
pytest evals/ -q --spotcheck  # also writes evals/spotcheck/*.md for a human to read
```

## What it grades, and what it does not

**It plays the product's own loop.** `orchestrator.create_adventure`, the real
prompt assembly, the real Learning Engine selection, the real safety pipeline,
the real fallbacks — and grades what comes out the far end. It is not a test of a
model's talent and does not pretend to be one.

In the default **scripted** mode the writer is `harness.EvalProvider`, which
answers by schema, deterministically, and can be told to answer badly. So what is
gated on every commit is the *pipeline*: does the selected objective actually
reach the beat that is delivered, does the companion stay one character, does the
close ask nothing, does an injected instruction reach an instruction layer, does
a blocked generation still leave a child with something to tap. Those are
properties of our code, they are what a prompt or router change breaks, and they
are checkable for nothing.

In **`--live`** mode the same scenarios run against the configured provider. That
is the end-to-end red-team pass a person does before a release, and the only mode
in which the age rubric's model-grader runs. It is deliberately **not** the gate:
a gate that depends on a vendor's afternoon goes red for reasons unrelated to the
change, and a gate that does that is one somebody eventually disables.

## The four suites

| File | §19.7 | Gate |
|---|---|---|
| `test_golden.py` | (a) coherence, objective exercised, companion consistency, warm close | 0.90–1.00 |
| `test_redteam.py` | (b) injection, unsafe topics, fear ceilings, distress, false positives | 1.00 |
| `test_age_rubric.py` | (c) rubric + model-grader + human spot-check | 0.90 / 0.85 |
| `test_regression.py` | (d) cases distilled from every `Docs/Review/reports/Day*-report.md` | 1.00 |
| `test_gate.py` | the gate itself | — |

Every threshold is in `thresholds.py`, with a comment saying whether it is an
absolute (safety: there is no acceptable rate) or a judgement (quality: a model
has off days). Changing one is covered by `Docs/Runbooks/Eval-Gate.md`.

## The two things that make it worth having

**Every grader has a test proving it bites.** A golden suite whose graders have
never once returned a failure is a suite that will go green through the
regression it was written for. `harness.REGRESSIONS` is a set of deliberate ways
to break the output, and `test_gate.py` asserts that each one is named by a test
somewhere that catches it.

**The gate is drilled, not assumed.** `PRIMER_EVAL_FORCE_REGRESSION=<name>` makes
the suite go red for a real reason, without an edit anyone has to remember to
revert. `Docs/Runbooks/Eval-Gate.md` is the drill, with the run ids from the last
time it was done.

## Layout

```
conftest.py     django + fixtures; the scripted/live switch
harness.py      EvalProvider, `play` (one adventure through the real loop), Score
graders.py      what "good" means, as code. Deterministic; returns a reason, not a bool
thresholds.py   every gate, with the reasoning for each number
scenarios        — there is no scenarios.py: the scenarios are the fixtures in
                  conftest.py plus the corpus in apps/safety/redteam/corpus.py,
                  which is shared with the backend suite on purpose. A second
                  list of attacks would be a second thing to keep up to date.
```

## Adding a case

1. Reproduce it. Add the string to `apps/safety/redteam/corpus.py` (safety) or a
   case to `test_regression.py` (anything a review found), **stated as the
   symptom a person saw** rather than as the mechanism — the mechanism changes
   under refactoring and the symptom must never come back.
2. Watch it fail. A case added after the fix never proved anything.
3. Fix it. Watch it pass.
