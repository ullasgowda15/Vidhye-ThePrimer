"""The safety layer every generation traverses (PRODUCT.md §13.2).

    inputs -> guard_input -> assemble+generate -> guard_output -> image scan -> child
                   |                                    |
                   v                                    v
              incident row                  rewrite / fallback / escalate

This module is the one §13.2 draws as a flowchart, written out. It owns three
public functions and nothing else, because the whole value of a safety *layer*
over a set of safety *checks* is that there is exactly one door.

**`guard_input`** — everything that is not ours is scrubbed before it is
assembled into a prompt (§13.5), and the child's own utterance is additionally
read for distress signals (§13.10).

**`guard_output`** — every beat is graded before it can reach a child, by a
deterministic policy check that always runs and an independent model classifier
that runs when configured (§13.3). The stronger of the two verdicts wins.

**`safe_fallback_beat`** — the pre-vetted warm beat a `block` resolves to
(§13.3, §19.6), re-exported from `apps.safety.fallbacks` so a caller needs to
know one module.

Two design commitments worth stating because they are easy to erode:

**A guard that could not run is not a pass.** If the classifier is unreachable,
the deterministic verdict stands, an incident is written with
`action=degraded`, and the log says so. Nothing anywhere converts "we did not
check" into "it is fine."

**No decision here is silent.** Every non-pass writes a `SafetyIncident`
(§13.11), because the rate at which these fire is the only evidence that any of
this works — and because the day something *does* reach a child, the question
will be "when did it start" and the answer has to be in a table.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.profiles.models import Child
from apps.safety import classifier, escalation, incidents, policy
from apps.safety.fallbacks import safe_fallback_beat
from apps.safety.models import Action, Severity
from apps.safety.policy import BLOCK, ESCALATE, PASS, REWRITE
from apps.safety.scrub import scrub, scrub_value

logger = logging.getLogger(__name__)

__all__ = [
    "BLOCK",
    "ESCALATE",
    "GUARD_KEY",
    "PASS",
    "REWRITE",
    "Decision",
    "guard_input",
    "guard_output",
    "max_rewrites",
    "safe_fallback_beat",
]

#: Where `guard_input` leaves its verdict in the dict it returns. Reserved,
#: documented, and popped by the caller before assembly — the prompt assembler
#: reads named keys (`memory`, `turn`) and would ignore this one anyway, but a
#: leading underscore makes "this is metadata, not prompt content" visible at
#: every call site rather than only here.
GUARD_KEY = "_guard"


@dataclass(frozen=True, slots=True)
class Decision:
    """What the safety layer decided about one generation.

    `action` is one of §13.2's four: `pass`, `rewrite`, `block`, `escalate`.
    `categories` and `markers` say *why* in terms a reviewer can triage —
    `markers` names the rules that fired and never the text that fired them
    (see `apps.safety.incidents`).
    """

    action: str = PASS
    severity: str = Severity.LOW
    categories: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    reason: str = ""
    #: True when the model classifier could not be reached, so this verdict rests
    #: on the deterministic policy alone. Recorded on the incident and in the log.
    degraded: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.action == PASS

    @property
    def escalates(self) -> bool:
        return self.action == ESCALATE

    @property
    def category(self) -> str:
        """The single label this decision is filed under. Worst rule first."""
        return self.categories[0] if self.categories else "unspecified"

    def as_detail(self) -> dict[str, Any]:
        """The incident's `detail` blob. Rule names only — never beat text."""
        payload: dict[str, Any] = {
            "action": self.action,
            "categories": list(self.categories),
            "markers": list(self.markers),
            "degraded": self.degraded,
            **self.detail,
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


def max_rewrites() -> int:
    """How many constrained regenerations a beat may cost before the fallback.

    Bounded because the child is waiting: §19.6 puts the cap at one repair for a
    *schema* failure, and a policy failure gets a little more room because the
    text is well-formed and the problem is usually a dial. Past the cap the
    fallback is strictly better than a fourth model call.
    """
    return max(0, int(settings.SAFETY_MAX_REWRITES))


def _from_findings(findings: list[policy.Finding], *, degraded: bool = False) -> Decision:
    """Fold a set of deterministic findings into one decision."""
    if not findings:
        return Decision(degraded=degraded)

    action = policy.worst(finding.action for finding in findings)
    severity = policy.worst_severity(finding.severity for finding in findings)
    # Ordered worst-first so `Decision.category` files the incident under the
    # thing that actually decided it.
    ordered = sorted(findings, key=lambda f: policy.ACTION_ORDER.index(f.action), reverse=True)
    return Decision(
        action=action,
        severity=severity,
        categories=tuple(dict.fromkeys(f.category for f in ordered)),
        markers=tuple(dict.fromkeys(f.marker for f in ordered)),
        degraded=degraded,
    )


# ---------------------------------------------------------------------------
# Input guard (§13.5, §13.10)
# ---------------------------------------------------------------------------
def guard_input(context: dict[str, Any], child: Child) -> dict[str, Any]:
    """Neutralise everything in `context` that did not come from us.

    `context` is the *prompt inputs*, not the assembled prompt: today
    `{"memory": [...], "turn": {...}}`, and on Day 8 whatever retrieval adds. It
    is guarded here — before assembly — so a new caller cannot reach the model
    without passing through this function, and so the scrub is applied once
    rather than once per layer.

    Returns a **new** dict with the same keys plus `GUARD_KEY`, carrying the
    guard's own `Decision`. The input is not modified: the caller may still need
    the raw turn for something that is not a prompt (`orchestrator.turn_from`'s
    `offered_labels` feeds a post-write check), and a guard that mutated its
    argument would silently change that too.

    The child's own words get a second pass. Anything the child said — a `say`
    answer today, free text and voice from Week 2 — is read for the distress
    signals of §13.10, and one found there escalates: the incident is written at
    critical severity and the parent-notify seam fires. That is the only branch in
    this file that is about the child rather than about the model.
    """
    guarded: dict[str, Any] = {}
    findings: list[str] = []

    for key, value in context.items():
        if key == GUARD_KEY:
            continue
        cleaned, found = scrub_value(value)
        guarded[key] = cleaned
        findings.extend(found)

    decision = _input_decision(context, child, tuple(dict.fromkeys(findings)))
    guarded[GUARD_KEY] = decision
    return guarded


def _input_decision(
    context: Mapping[str, Any],
    child: Child,
    findings: tuple[str, ...],
) -> Decision:
    """The verdict on the inputs, and the incidents that go with it."""
    distress = policy.check_child_utterance(_child_said(context))
    if distress:
        decision = _from_findings(distress)
        incident = incidents.record(
            category=decision.category,
            action=Action.ESCALATED,
            severity=Severity.CRITICAL,
            child=child,
            detail={**decision.as_detail(), "surface": "child_input"},
            escalated=False,
        )
        if incident is not None:
            escalation.notify_parent(incident)
        return decision

    if not findings:
        return Decision()

    # Injection and PII in the inputs are *neutralised*, not refused: the text is
    # already gone by the time this runs, and blocking the story on top of that
    # would let a hostile profile field deny a child their storybook (§13.5).
    logger.warning("safety.input_scrubbed child=%s findings=%s", child.id, ",".join(findings))
    incidents.record(
        category=findings[0].split(":")[0],
        action=Action.SCRUBBED,
        severity=Severity.MEDIUM,
        child=child,
        detail={"markers": list(findings), "surface": "prompt_inputs"},
    )
    return Decision(
        action=PASS,
        severity=Severity.MEDIUM,
        categories=tuple(dict.fromkeys(f.split(":")[0] for f in findings)),
        markers=findings,
    )


def _child_said(context: Mapping[str, Any]) -> str:
    """Everything in the inputs that the *child* themselves produced.

    Narrow on purpose. An option label is a model's words and a `next_hint` is a
    model's words; only `value` and `text` on the turn came from a person, and
    escalating on anything else would fire a parent alert about a sentence the
    product wrote.
    """
    turn = context.get("turn") or {}
    if not isinstance(turn, Mapping):
        return ""
    return " ".join(str(turn.get(key)) for key in ("value", "text") if turn.get(key) is not None)


def scrub_profile_field(value: str, *, cap: int | None = None) -> str:
    """One parent-typed field, safe to render into a prompt layer.

    Exposed for `prompting.assembler`, which renders `avoid_topics`, `fears` and
    the companion's descriptor into **instruction** layers — the one place where
    untrusted text sits above the data fence and where the scrub is therefore
    doing its most important work (§13.5, criterion 3's "profile fields").
    """
    return scrub(value).text if cap is None else scrub(value, cap=cap).text


# ---------------------------------------------------------------------------
# Output guard (§13.3)
# ---------------------------------------------------------------------------
def child_facing_text(beat: Any) -> str:
    """Everything of a beat that a child sees or hears, as one string.

    The narration, the spoken question and every option label — because they are
    spoken in one clip (`orchestrator.spoken_line`) and read as one screen, so a
    rule broken across two of them is still a rule broken. The illustration prompt
    is included too: it is not read aloud, but it is what an image model is about
    to draw, and "no weapons" has to hold for the picture as much as the words.
    """
    interaction = getattr(beat, "interaction", None)
    parts = [str(getattr(beat, "narration", "") or "")]

    if interaction is not None:
        prompt_audio = getattr(interaction, "prompt_audio", None)
        if prompt_audio:
            parts.append(str(prompt_audio))
        for option in getattr(interaction, "choices", None) or []:
            parts.append(str(getattr(option, "label", "") or ""))
            parts.append(str(getattr(option, "icon_prompt", "") or ""))

    scene = getattr(beat, "scene", None)
    if scene is not None:
        parts.append(str(getattr(scene, "illustration_prompt", "") or ""))
        parts.append(str(getattr(scene, "setting", "") or ""))

    return "\n".join(part for part in parts if part)


def guard_output(beat: Any, child: Child) -> Decision:
    """Grade one generated beat against the policy (§13.3). **Never self-grading.**

    `beat` is the generated *contract* (`story.ai.schemas.Beat`), checked before
    anything is persisted — so a blocked beat leaves no row, no media task and no
    trace on the adventure other than its incident.

    Two guards, and the stronger verdict wins:

    * `apps.safety.policy` — deterministic, no network, always runs, high
      precision. It is what makes this function testable in CI with no key.
    * `apps.safety.classifier` — an independent model call against a separate
      prompt and a separate model. It is the one that can read subtext.

    A classifier that cannot be reached leaves `degraded=True` on the returned
    decision and writes its own incident. It never yields a pass.
    """
    text = child_facing_text(beat)
    findings = policy.check_text(text, child)
    deterministic = _from_findings(findings)

    try:
        action, severity, categories, reason = classifier.classify(text, child)
    except classifier.ClassifierUnavailable as exc:
        return _degraded(deterministic, child, str(exc))

    logger.info(
        "safety.classifier child=%s %s",
        child.id,
        classifier.dump_for_log(action, severity, categories, reason),
    )

    combined = Decision(
        action=policy.worst((deterministic.action, action)),
        severity=policy.worst_severity((deterministic.severity, severity)),
        categories=tuple(dict.fromkeys((*deterministic.categories, *categories))),
        markers=deterministic.markers,
        reason=reason,
        detail={"classifier": action},
    )
    return combined


def _degraded(deterministic: Decision, child: Child, why: str) -> Decision:
    """The independent guard did not run. Say so; do not pretend it passed.

    The incident is written only when the classifier was *supposed* to run: an
    installation with `SAFETY_CLASSIFIER_ENABLED` off (CI, offline development)
    is a deliberate configuration and would otherwise write one row per beat,
    which is how an incident queue becomes something nobody reads.
    """
    if classifier.is_enabled():
        logger.error("safety.classifier_unavailable child=%s reason=%s", child.id, why)
        incidents.record(
            category=policy.CLASSIFIER_UNAVAILABLE,
            action=Action.DEGRADED,
            severity=Severity.MEDIUM,
            child=child,
            detail={"reason": why[:300]},
        )
    else:
        logger.debug("safety.classifier_disabled child=%s", child.id)

    return Decision(
        action=deterministic.action,
        severity=deterministic.severity,
        categories=deterministic.categories,
        markers=deterministic.markers,
        degraded=True,
    )


# ---------------------------------------------------------------------------
# Acting on a decision
# ---------------------------------------------------------------------------
def report(
    decision: Decision,
    *,
    child: Child,
    adventure: Any = None,
    beat: Any = None,
    seq: int | None = None,
    model: str = "",
) -> None:
    """Write the incident for a non-pass, and escalate if it is one (§13.10, §13.11).

    Called by the orchestrator once it has acted, so `action_taken` records what
    the child actually got rather than what the guard suggested. An ordinary pass
    writes nothing: an incident per beat would bury the queue.

    **A beat that passed only after a rewrite is still a non-pass**, and files
    under `rewritten`. The refusal happened, it cost a generation, and its rate is
    precisely what §19.7's monitoring watches — a guard whose successful retries
    were invisible would report a flat incident rate while quietly failing more
    and more often.
    """
    rewrites = int(decision.detail.get("rewrites") or 0)
    if decision.passed and not rewrites:
        return

    action = Action.REWRITTEN if decision.passed else incidents.action_for(decision.action)
    incident = incidents.record(
        category=decision.category,
        action=action,
        severity=decision.severity,
        child=child,
        adventure=adventure,
        beat=beat,
        detail={**decision.as_detail(), "seq": seq, "model": model},
    )
    if incident is not None and decision.escalates:
        escalation.notify_parent(incident)
