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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.profiles.models import Child
from apps.safety import classifier, escalation, incidents, policy
from apps.safety.fallbacks import safe_fallback_beat
from apps.safety.models import Action, SafetyIncident, Severity
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
    "guard_child_utterance",
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

#: What `Decision.detail["classifier"]` says when the independent guard was
#: configured to run and could not. Distinguished from `CLASSIFIER_DISABLED`
#: because only one of the two is a fault: an installation that switched the
#: classifier off chose that (and `prod.py` refuses to boot having chosen it),
#: while one that *tried and failed* has a beat nothing strong ever graded.
CLASSIFIER_UNAVAILABLE = "unavailable"
CLASSIFIER_DISABLED = "disabled"


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

    @property
    def ungraded(self) -> bool:
        """The independent classifier was **meant** to run on this and did not.

        Narrower than `degraded`, which is also true when the classifier is
        deliberately switched off. The two need separating because they land in
        different places: `degraded` is the honest statement about *this verdict*
        and rides on every incident, while this one is what `Beat.safety_status`
        records — and a column that said `degraded` on every beat of every CI run
        would say nothing at all, the same way an incident per beat is how a queue
        stops being read (`_degraded`).
        """
        return self.detail.get("classifier") == CLASSIFIER_UNAVAILABLE

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
def guard_input(
    context: dict[str, Any], child: Child, *, utterance_ref: str = ""
) -> dict[str, Any]:
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

    The child's own words get a second pass — **second**, deliberately. Since the
    Day-7 fix the distress check runs where the utterance *arrives*
    (`guard_child_utterance`, called by `orchestrator.record_choice`), because a
    disclosure that is only read when the next beat is generated is a disclosure
    dropped whenever there is no next beat: the close, the child leaving, a
    prefetched beat. This pass stays because defence in depth is the whole design
    of §13.2 and because it covers a caller that reaches generation another way;
    `utterance_ref` is what stops the two passes filing two alerts about one
    sentence.
    """
    guarded: dict[str, Any] = {}
    findings: list[str] = []

    for key, value in context.items():
        if key == GUARD_KEY:
            continue
        cleaned, found = scrub_value(value)
        guarded[key] = cleaned
        findings.extend(found)

    decision = _input_decision(
        context, child, tuple(dict.fromkeys(findings)), utterance_ref=utterance_ref
    )
    guarded[GUARD_KEY] = decision
    return guarded


def guard_child_utterance(
    value: Any,
    child: Child,
    *,
    ref: str = "",
    surface: str = "child_input",
    adventure: Any = None,
    beat: Any = None,
) -> Decision:
    """Read something the **child** said for the distress signals of §13.10.

    Called at every point a child's words enter the system, not at the point a
    beat happens to be generated. Those were the same place until Day 7's review
    proved they are not: a disclosure typed as the answer to the warm close, or
    said just before the child closes the tab, or said when the next beat had
    already been prefetched, produces no generation at all — and produced no
    incident either, on three of the four ingress paths.

    Returns the `Decision` so the caller can de-escalate the story if it is in a
    position to; the alert does not wait for that and never has. **Never raises.**
    A guard that took the story down would turn "a child said something worrying"
    into "the app stopped working", which is the worst possible pairing
    (`apps.safety.incidents` makes the same argument about the incident write).

    `ref` identifies the *utterance*, so the same sentence read twice — once here
    and once by `guard_input` on the next generation — is one incident and one
    parent alert. An alert a family learns to ignore is worse than no alert
    (§13.10), and that includes learning to ignore it because it arrives twice.
    """
    try:
        findings = policy.check_child_utterance(_utterance_text(value))
    except Exception:
        # See "never raises" above. There is nothing else to do with a payload we
        # cannot even read as text, but taking the story down over it is not it.
        logger.exception("safety.utterance_unreadable child=%s surface=%s", child.id, surface)
        return Decision()
    if not findings:
        return Decision()

    decision = _from_findings(findings)
    if _already_escalated(child, ref):
        logger.info("safety.escalation_deduped child=%s surface=%s ref=%s", child.id, surface, ref)
        return decision

    incident = incidents.record(
        category=decision.category,
        action=Action.ESCALATED,
        severity=Severity.CRITICAL,
        child=child,
        adventure=adventure,
        beat=beat,
        # `ref` is an opaque row id, not the child's words — the rule in
        # `apps.safety.incidents` holds: this table records what fired, never what
        # fired it.
        detail={**decision.as_detail(), "surface": surface, "ref": ref},
        escalated=False,
    )
    if incident is not None:
        escalation.notify_parent(incident)
    return decision


def _already_escalated(child: Child, ref: str) -> bool:
    """Has this exact utterance already raised the alarm?

    Swallows its own failure on purpose. If the dedupe query cannot run, the
    honest direction is a second alert rather than a missed one — so a broken
    read here degrades to the pre-fix behaviour and never to silence.
    """
    if not ref:
        return False
    try:
        return SafetyIncident.objects.filter(
            child=child,
            category=policy.DISTRESS,
            action_taken=Action.ESCALATED,
            detail__ref=ref,
        ).exists()
    except Exception:
        logger.exception("safety.escalation_dedupe_failed child=%s ref=%s", child.id, ref)
        return False


def _input_decision(
    context: Mapping[str, Any],
    child: Child,
    findings: tuple[str, ...],
    *,
    utterance_ref: str = "",
) -> Decision:
    """The verdict on the inputs, and the incidents that go with it."""
    distress = guard_child_utterance(
        _child_said(context), child, ref=utterance_ref, surface="child_input"
    )
    if distress.escalates:
        return distress

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
    return " ".join(
        _utterance_text(turn.get(key)) for key in ("value", "text") if turn.get(key) is not None
    )


def _utterance_text(value: Any) -> str:
    """Whatever the child sent, flattened to text the distress rules can read.

    `Choice.value` is a `JSONField` and the client decides its shape: a string
    today, and from Week 2 a transcript object or a list of them. Walking the
    structure rather than calling `str()` on it is what keeps a disclosure nested
    one level down from being checked as `{'text': 'my dad hits me'}` — which
    happens to match, and would stop matching the day someone adds a key.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_utterance_text(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return " ".join(_utterance_text(item) for item in value)
    return str(value)


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
    except Exception as exc:
        # `classify` already converts everything it can reach into the line above.
        # This is the second lock on the same door: this function is on the child's
        # path, the thing above it renders no error state (§4.9), and the review
        # that found P0-1 found it precisely because one exception class nobody had
        # thought about walked through a handler that named only one other.
        logger.exception("safety.guard_output_crashed child=%s", child.id)
        return _degraded(deterministic, child, f"{type(exc).__name__}: {exc}")

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
    enabled = classifier.is_enabled()
    if enabled:
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
        # Carried on the decision rather than re-derived by the caller, so the
        # beat row and the incident agree about whether the strong guard was even
        # attempted. See `Decision.ungraded`.
        detail={"classifier": CLASSIFIER_UNAVAILABLE if enabled else CLASSIFIER_DISABLED},
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
