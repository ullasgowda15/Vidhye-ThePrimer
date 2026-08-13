"""The gate itself (PRODUCT.md §19.7).

> *"Ship-gating: a change that regresses safety or coherence beyond threshold
> cannot deploy."*

The rest of this suite grades the product. This file grades **the gate**, which is
a different and easily-forgotten job: a threshold nobody has ever seen bite is a
threshold that might be `>=` where it should be `>`, might be reading an empty
score as a perfect one, or might be gating on a grader that silently stopped
running. All three ship green.

So this file asserts the machinery, in-process, on every commit:

* a score below its threshold **fails**, and says which cases;
* a score at its threshold passes, and one hair below does not;
* a grader that ran on nothing scores **zero**, never one;
* every gate the suite claims to enforce is declared, and every declared gate is
  actually asserted somewhere.

The end-to-end half of the proof — force a regression, watch CI go red, watch the
deploy job refuse to run — is a drill rather than a test, because "the deploy was
blocked" is a fact about GitHub and not about Python. It is written up in
`Docs/Runbooks/Eval-Gate.md`, with the run ids from the time it was actually done.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import thresholds
from harness import REGRESSIONS, Score


class TestAScoreBelowItsGateFails:
    def test_it_fails_and_names_the_cases(self) -> None:
        score = Score("demo", passed=8, total=10, failures=["beat 3 ended on a cliffhanger"])
        with pytest.raises(pytest.fail.Exception) as caught:
            score.assert_at_least(0.90)

        message = str(caught.value)
        assert "8/10 = 80%" in message
        assert "below the 90% gate" in message
        # The cases, not just the number. When the gate goes red this message is
        # the only thing anybody reads.
        assert "cliffhanger" in message

    def test_exactly_at_the_gate_passes(self) -> None:
        Score("demo", passed=9, total=10).assert_at_least(0.90)

    def test_one_case_below_the_gate_does_not(self) -> None:
        with pytest.raises(pytest.fail.Exception):
            Score("demo", passed=8, total=10).assert_at_least(0.90)

    def test_a_grader_that_graded_nothing_scores_zero(self) -> None:
        """The failure that ships green.

        A harness bug that stops a grader ever being called leaves `0/0`, and
        `0/0 = 1.0` is the most natural thing to write. It would mean every gate
        in this suite passes on a suite that is doing nothing at all.
        """
        empty = Score("demo")
        assert empty.rate == 0.0
        with pytest.raises(pytest.fail.Exception):
            empty.assert_at_least(0.01)


class TestTheThresholdsAreCoherent:
    def test_every_gate_is_a_fraction(self) -> None:
        for name, value in thresholds.GATES.items():
            assert 0.0 < value <= 1.0, f"{name} has a nonsensical gate: {value}"

    def test_safety_gates_are_absolute(self) -> None:
        """§13 admits no rate. Written as an assertion so that lowering one is a
        deliberate act with this test in the diff, rather than a number edit
        somebody makes to get a build green on a Friday."""
        for name, value in thresholds.GATES.items():
            if name.startswith("redteam.") or name.startswith("regression."):
                assert value == 1.00, (
                    f"{name} is gated at {value:.0%}. Safety and regression gates are "
                    f"absolutes — see thresholds.py's docstring before changing this."
                )

    def test_an_undeclared_gate_is_an_error_and_not_zero(self) -> None:
        """A missing gate resolving to 0.0 is a grader that runs, scores nothing
        and passes."""
        with pytest.raises(KeyError):
            thresholds.gate_for("golden.a_grader_nobody_declared")

    def test_every_declared_gate_is_actually_asserted_somewhere(self) -> None:
        """The gate that stops the *gate* rotting.

        A threshold in `GATES` that no test reads is a claim the suite does not
        make. This greps the suite for each name, which is crude and is exactly
        strong enough: a grader that is deleted or renamed takes its gate's only
        reference with it and fails here.
        """
        suite = "\n".join(
            path.read_text(encoding="utf-8") for path in Path(__file__).parent.glob("test_*.py")
        )
        unused = [
            name
            for name in thresholds.GATES
            if f'gate_for("{name}")' not in suite and f'Score("{name}"' not in suite
        ]
        # The live-only model grader is asserted inside a test that skips without
        # `--live`; it is still referenced, which is what this is checking.
        assert not unused, f"declared but never enforced: {unused}"


class TestTheDrillIsReal:
    def test_the_forced_regression_switch_is_documented_and_wired(self) -> None:
        """`PRIMER_EVAL_FORCE_REGRESSION` is how a person reproduces the gate
        firing without editing code and remembering to revert it. If the name in
        the runbook and the name in `conftest` ever drift, the drill silently
        does nothing and the suite passes — which is the exact outcome the drill
        exists to disprove.
        """
        import conftest

        runbook = Path(__file__).parents[1] / "Docs" / "Runbooks" / "Eval-Gate.md"
        assert runbook.is_file(), "the gate drill has no runbook"
        assert conftest.FORCE_REGRESSION_ENV in runbook.read_text(encoding="utf-8")

    def test_every_regression_the_harness_offers_is_caught_by_some_grader(self) -> None:
        """Each entry in `harness.REGRESSIONS` is a way to break the output on
        purpose. One that no grader catches is a hole in the suite advertised as
        a feature — so each must be named by a test that proves a grader bites.
        """
        suite = "\n".join(
            path.read_text(encoding="utf-8") for path in Path(__file__).parent.glob("test_*.py")
        )
        uncaught = [name for name in REGRESSIONS if f'regress("{name}")' not in suite]
        assert not uncaught, (
            f"these regressions can be produced but nothing proves a grader catches them: "
            f"{uncaught}"
        )

    def test_the_deploy_workflow_actually_requires_this_suite(self) -> None:
        """The half of the gate that lives in YAML.

        Everything else in this file could be perfect and the gate would still be
        decorative if the deploy job did not wait on it. So the chain is checked
        link by link, because it is exactly three links long and a CI refactor
        can quietly remove any one of them:

            release --needs--> gate --uses--> ci.yml --has--> the `evals` job

        Crude string matching on YAML, deliberately: parsing it properly would
        need a dependency in the eval suite, and what is being asserted is the
        presence of three specific strings that a person would have to delete on
        purpose.
        """
        workflows = Path(__file__).parents[1] / ".github" / "workflows"
        deploy_path, ci_path = workflows / "deploy.yml", workflows / "ci.yml"
        assert deploy_path.is_file(), "there is no deploy workflow for the evals to gate"

        deploy = deploy_path.read_text(encoding="utf-8")

        needs = re.search(r"needs:\s*\[([^\]]*)\]", deploy)
        assert needs, "the release job declares no `needs:`; nothing gates it"
        assert "gate" in needs.group(
            1
        ), f"the release job waits on {needs.group(1)!r}, which does not include the gate"

        assert re.search(r"gate:\s*\n(?:.*\n)*?\s*uses:\s*\./\.github/workflows/ci\.yml", deploy), (
            "the deploy's `gate` job does not call ci.yml, so nothing re-runs the "
            "suites on the tagged commit"
        )

        assert re.search(r"^  evals:", ci_path.read_text(encoding="utf-8"), re.M), (
            "ci.yml has no `evals` job, so the gate the release waits on does not "
            "include the eval suite"
        )

        # The flag that turns a gate into a decoration. `ci.yml` uses it once, on
        # mypy, with a comment saying so; the deploy must not use it at all.
        assert "continue-on-error" not in deploy, (
            "the deploy workflow contains `continue-on-error`, which can only ever "
            "weaken the gate"
        )
