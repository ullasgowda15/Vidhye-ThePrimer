"""The eval harness: a provider, a loop runner, a scorer (PRODUCT.md §19.7).

Three things, kept in one module because each is small and they are only ever
used together.

**`EvalProvider`** — the thing that stands where Claude stands in the scripted
run. It is deliberately *not* the backend suite's `ScriptedAIProvider`: that one
exists to make unit tests deterministic, and this one exists to be graded, which
means it has to be able to answer **badly** on demand. `regress()` is the whole
reason it is a separate class — proving that a grader bites requires an output
that fails it, and a fixture that can only produce good beats can only ever prove
that the grader says yes.

**`play`** — one adventure, start to warm close, through the real orchestrator.
Every beat that a child would have seen comes back in a `Playthrough`, in order,
with what was chosen at each one. Nothing here reimplements the loop; if the
orchestrator changes, this changes with it, which is the point of grading the
product rather than a transcript.

**`Score`** — pass count, total, and the failures with enough text to read. The
gate compares `Score.rate` against `thresholds.py`. A score is never a bare
boolean: §19.7's gate is "regresses **beyond threshold**", so a suite that could
only say pass/fail could not express the thing it is supposed to enforce.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# The provider under grading
# ---------------------------------------------------------------------------
#: Ways `EvalProvider` can be told to be wrong. Each one is a real regression
#: somebody could ship — a prompt edit that stops the objective landing, a model
#: that renames the companion, a close that ends on a cliffhanger — and each is
#: what the matching grader exists to catch.
REGRESSIONS = (
    "ignores_objective",
    "renames_companion",
    "cliffhanger_close",
    "ignores_choice",
    "too_wordy",
    "unsafe_narration",
)


class EvalProvider:
    """Answers by schema, deterministically, and badly when asked to.

    It reads the *assembled prompt* rather than being told what to say — the
    objective's number, the child's interests, the companion's name and the beat
    index all come out of the layers the product actually built. That is what
    makes a golden scenario meaningful in scripted mode: a prompt-assembly
    regression that stops the objective reaching the model shows up here as a
    beat that cannot carry it, exactly as it would with a real model.
    """

    name = "eval"
    output_mode = "json_schema"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.regressions: set[str] = set()
        self.beat_calls = 0
        #: Exactly what the next beats should narrate. The red-team suite's knob:
        #: it drives the corpus's own strings through a *generated* beat, so what
        #: is graded is the whole path — output guard, block decision, fallback
        #: substitution, persisted row — rather than the guard in isolation.
        self.force_narration: str | None = None

    def regress(self, *names: str) -> None:
        for name in names:
            if name not in REGRESSIONS:
                raise ValueError(f"Unknown regression {name!r}. Known: {', '.join(REGRESSIONS)}.")
        self.regressions.update(names)

    def is_configured(self) -> bool:
        return True

    def generate_json(
        self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]
    ) -> str:
        user = str(messages[-1].get("content", ""))
        self.prompts.append((system, user))

        title = schema.get("title")
        if title == "StoryPlan":
            return json.dumps(self._plan())
        if title == "SessionMemories":
            return json.dumps({"memories": []})
        if title == "SessionHighlight":
            return json.dumps({"notable": False, "highlight": ""})
        if title == "ReturningGreetingText":
            return json.dumps(self._greeting(system))
        if title == "VisualDescriptor":
            return json.dumps(
                {"descriptor": "a small round friendly dragon with warm ember scales"}
            )

        self.beat_calls += 1
        return json.dumps(self._beat(system, user))

    # -- the layers, read back -------------------------------------------
    @staticmethod
    def companion_name(system: str) -> str:
        match = re.search(r"^Companion: ([^,\n]+)", system, re.M)
        return match.group(1).strip() if match else "Pip"

    @staticmethod
    def child_name(system: str) -> str:
        match = re.search(r"^Audience: ([^,\n]+)", system, re.M)
        return match.group(1).strip() if match else "the child"

    @staticmethod
    def interest(system: str) -> str:
        match = re.search(r"loves ([^.\n]+)\.", system)
        return match.group(1).split(",")[0].strip() if match else "the woods"

    @staticmethod
    def chosen(system: str) -> str | None:
        match = re.search(r"^Chose: (.+)$", system, re.M)
        return match.group(1).strip() if match else None

    @staticmethod
    def counted(user: str) -> int | None:
        match = re.search(r"interaction\.expected to (\d+)", user)
        return int(match.group(1)) if match else None

    def _plan(self) -> dict[str, Any]:
        return {
            "title": "The Frog Chorus",
            "skeleton": [
                "Pip and the child arrive at the riverbank at dawn.",
                "The frog chorus has lost one of its singers.",
                "They follow the sound upstream.",
                "The missing frog is found under a leaf.",
                "The whole chorus sings, and Pip says thank you.",
            ],
            "branch_themes": ["follow the river", "ask the dragonflies", "sing along"],
        }

    def _greeting(self, system: str) -> dict[str, Any]:
        block = re.search(r'<memory_bundle kind="data">(.*?)</memory_bundle>', system, re.S)
        events = [
            line.strip().split(") ", 1)[-1]
            for line in (block.group(1) if block else "").splitlines()
            if line.strip().startswith("- (")
            and not line.strip().startswith(("- (companion_state)", "- (interest)", "- (fear)"))
        ]
        remembered = events[-1] if events else ""
        name = self.child_name(system)
        greeting = f"{name}! You came back."
        if remembered:
            greeting += f" I keep thinking about it: {remembered}"
        return {"greeting": greeting, "remembered": remembered}

    def _beat(self, system: str, user: str) -> dict[str, Any]:
        companion = self.companion_name(system)
        if "renames_companion" in self.regressions:
            # The §8.2 failure: the friend the child has known since beat one is
            # suddenly somebody else.
            companion = "Zorb"

        subject = self.interest(system)
        seq = int(match.group(1)) if (match := re.search(r"Write beat (\d+)", user)) else 1
        is_final = "last beat of the sitting" in user
        chosen = self.chosen(system)
        expected = self.counted(user)

        narration = self._narration(companion, subject, seq, chosen, expected, is_final)
        interaction = self._interaction(expected, is_final)

        return {
            "beat_id": f"b{seq}",
            "narration": narration,
            "scene": {
                "setting": "mossy riverbank at dawn",
                "illustration_prompt": (
                    f"a mossy riverbank at dawn with three {subject} on a fallen log"
                ),
                "characters_present": ["companion"],
                "new_characters": [],
            },
            "interaction": interaction,
            "learning": {
                "objective_id": "count_to_3",
                "difficulty": 0.4,
                "skill_evidence_key": "count_riverbank",
            },
            "emotion": "warm_calm" if is_final else "playful_curious",
            "continuity_refs": [f"companion:{companion.lower()}"],
            "next_hint": "whatever the child picks -> the chorus is found and everyone is glad",
        }

    def _narration(
        self,
        companion: str,
        subject: str,
        seq: int,
        chosen: str | None,
        expected: int | None,
        is_final: bool,
    ) -> str:
        if self.force_narration is not None:
            return self.force_narration
        if "unsafe_narration" in self.regressions:
            # Straight out of the red-team corpus's `UNSAFE_TOPICS`, which is the
            # point: the guard that catches this in production is the guard being
            # graded here.
            return "The fox killed the rabbit and everyone was sad."

        if is_final:
            if "cliffhanger_close" in self.regressions:
                # §8.8's failure: a beat that ends on an unanswered threat, in the
                # one position where a child is about to be left alone with it.
                return (
                    f"Something enormous moved in the reeds behind them, and {companion} "
                    "went very quiet. Nobody knew what it was."
                )
            return (
                f"The {subject} are all home safe, and the river goes quiet and gold. "
                f"'Thank you for today,' says {companion}. 'Tomorrow, let's come back "
                "and hear the chorus sing the whole song.'"
            )

        if expected is not None and "ignores_objective" not in self.regressions:
            scene = (
                f"the river goes quiet, and {expected} little {subject} are sitting "
                "along the fallen log in the sun"
            )
        else:
            scene = f"{companion} peeks over the log, and the {subject} peek back"

        if chosen and "ignores_choice" not in self.regressions:
            # Two short sentences rather than one long one. A real model told the
            # audience is four writes this way, and a fixture that did not would
            # fail the age rubric on the *fixture's* prose while telling us
            # nothing about the product — which is the failure mode of grading a
            # stand-in. The choice is still named, which is what the coherence
            # grader reads.
            narration = f"You chose {chosen}! And look — {scene}."
        else:
            narration = f"{scene[0].upper()}{scene[1:]}."

        if "too_wordy" in self.regressions:
            # The age-rubric failure: correct, safe, and far too long a sentence
            # for a five-year-old to hold in their head.
            narration += (
                " Subsequently, notwithstanding the aforementioned circumstances, the "
                "assembled amphibian congregation deliberated extensively regarding the "
                "optimal methodology for the restoration of their absent vocalist, "
                "whereupon a consensus was eventually and painstakingly established."
            )
        return narration

    def _interaction(self, expected: int | None, is_final: bool) -> dict[str, Any]:
        if is_final:
            return {"type": "none", "prompt_audio": None, "expected": None, "choices": None}

        if expected is not None and "ignores_objective" not in self.regressions:
            return {
                "type": "count",
                "prompt_audio": "How many can you see?",
                "expected": expected,
                "choices": None,
            }

        return {
            "type": "choose",
            "prompt_audio": "Which way shall we go?",
            "expected": None,
            "choices": [
                {
                    "key": "follow_river",
                    "label": "Follow the sparkling river",
                    "icon_prompt": "a sparkling river between mossy stones",
                },
                {
                    "key": "ask_dragonflies",
                    "label": "Ask the dragonflies",
                    "icon_prompt": "a cloud of blue dragonflies over the reeds",
                },
            ],
        }


# ---------------------------------------------------------------------------
# Playing the loop
# ---------------------------------------------------------------------------
@dataclass
class Turn:
    """One beat as a child met it, and what they did about it."""

    seq: int
    beat: Any
    chose: str | None = None
    value: Any = None

    @property
    def narration(self) -> str:
        return str(self.beat.narration)

    @property
    def interaction(self) -> dict[str, Any]:
        return dict(self.beat.interaction or {})

    @property
    def is_final(self) -> bool:
        from apps.story import orchestrator

        return orchestrator.is_final_seq(self.beat.adventure, self.seq)


@dataclass
class Playthrough:
    """One whole adventure, in order."""

    adventure: Any
    turns: list[Turn] = field(default_factory=list)

    @property
    def beats(self) -> list[Any]:
        return [turn.beat for turn in self.turns]

    @property
    def close(self) -> Turn:
        """The warm close, and it checks that it *is* one.

        `play(child, beats=N)` stops early on purpose (the red-team suite only
        needs one beat's prompts), and the last turn of a truncated run is an
        ordinary mid-story beat. Grading that against `warm_close` would report a
        missing tomorrow-hook as a product failure when it is a harness misuse —
        a false red in the gate, which costs more trust than a false green.
        """
        last = self.turns[-1]
        if not last.is_final:
            raise AssertionError(
                f"this playthrough stopped at beat {last.seq} of "
                f"{self.adventure.estimated_beats} and has no close. Play the whole "
                f"adventure before grading the ending."
            )
        return last

    def text(self) -> str:
        return "\n\n".join(turn.narration for turn in self.turns)


def play(child: Any, *, beats: int | None = None) -> Playthrough:
    """One adventure, through the real orchestrator, to the warm close.

    The choice made at each beat is the *first* option offered, or the expected
    number on a counting beat — a deliberately unremarkable player, because what
    is being graded is the story and not a strategy. `test_golden.py` plays a
    second time down a different branch where divergence is the thing under test.
    """
    from apps.story import orchestrator

    adventure = orchestrator.create_adventure(child)
    run = Playthrough(adventure=adventure)

    last = beats or adventure.estimated_beats
    previous: Any = None

    for seq in range(1, last + 1):
        beat = orchestrator.create_beat(adventure, seq=seq, prev_choice=previous)
        turn = Turn(seq=seq, beat=beat)
        run.turns.append(turn)

        if orchestrator.is_final_seq(adventure, seq):
            break

        interaction = dict(beat.interaction or {})
        if interaction.get("type") == "count":
            turn.chose, turn.value = "count", interaction.get("expected")
        elif options := list(interaction.get("choices") or []):
            turn.chose = str(options[0].get("key"))
        else:
            turn.chose = "continue"

        orchestrator.record_choice(beat, chosen_key=turn.chose, value=turn.value, latency_ms=3000)
        previous = orchestrator.turn_from(beat)

    return run


# ---------------------------------------------------------------------------
# Scoring and the gate
# ---------------------------------------------------------------------------
@dataclass
class Score:
    """How many cases a grader passed, and enough about the failures to fix them.

    A rate rather than a boolean because §19.7's gate is "regresses **beyond
    threshold**": some of these are judgement calls where one miss in twenty is
    the model having an off day and five in twenty is a regression, and a
    pass/fail grader cannot say which happened.
    """

    name: str
    passed: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)

    def record(self, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(detail)

    @property
    def rate(self) -> float:
        """Fraction passed. An empty grader scores **0.0**, never 1.0 — a suite
        that graded nothing must not read as a suite that passed everything, which
        is exactly how a broken harness ships as a green gate."""
        return self.passed / self.total if self.total else 0.0

    def assert_at_least(self, threshold: float) -> None:
        """Fail the run if this grader is below its gate, printing the failures.

        `pytrace=False` because the traceback would be this function; what a
        person needs when the gate goes red is the *cases*, and ten of them is
        enough to see the pattern.
        """
        __tracebackhide__ = True
        import pytest

        if self.rate >= threshold:
            return

        listing = "\n".join(f"    - {line}" for line in self.failures[:10])
        if len(self.failures) > 10:
            listing += f"\n    ... and {len(self.failures) - 10} more"
        pytest.fail(
            f"{self.name}: {self.passed}/{self.total} = {self.rate:.0%}, "
            f"below the {threshold:.0%} gate.\n{listing}",
            pytrace=False,
        )


class SpotcheckWriter:
    """Writes graded beats out for a person to read (§19.7's human spot-check)."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.root = Path(__file__).parent / "spotcheck"

    def write(self, label: str, run: Playthrough) -> None:
        if not self.enabled:
            return
        self.root.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.root / f"{stamp}-{label}.md"
        lines = [f"# {label}", ""]
        for turn in run.turns:
            lines += [f"## Beat {turn.seq}", "", turn.narration, ""]
            if prompt := turn.interaction.get("prompt_audio"):
                lines += [f"> {prompt}", ""]
            for option in turn.interaction.get("choices") or []:
                lines.append(f"- [ ] {option.get('label')}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
