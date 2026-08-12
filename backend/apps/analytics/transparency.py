"""The transparency log (PRODUCT.md §12.6, §13.8).

§12.6 is the one feature in the parent surface that is a headline rather than a
line item: *the parent can read exactly what their child experienced — the actual
stories, verbatim*. The number-one parent anxiety about AI and children is "what is
it saying to my child", and the only answer that earns trust is showing them.

So this module is deliberately thin, and its thinness is the feature. It reads
`story.Beat` and `story.Choice` and returns them. **Nothing is summarised,
softened, re-ordered or omitted.** A beat that the safety layer rewrote is shown as
the rewritten text the child actually saw, labelled as rewritten; a beat the child
never reached is not in the log, because they did not experience it. If this file
ever grows a filter, the feature is gone and only the claim is left.

Two things it does add, and both are about *what happened* rather than about the
prose:

* **`safety_status`**, so a parent can see the safety layer working rather than
  take our word for it (§13.2). Hiding it would be the more comfortable choice and
  the wrong one — a rewritten beat is the system doing its job, and a product that
  only shows the parts that went smoothly is back to asking for trust by claim.
* **what the child did**, under the beat that asked. A transcript of our narration
  alone is half the conversation, and the half that is not their child.

Ownership is not checked here. It is checked at the view, against the child, before
this module is called at all — the same rule the memory endpoints follow (§11.10).
"""

from __future__ import annotations

from typing import Any

from apps.story.models import Adventure, Beat, Choice


def transcript_for(adventure: Adventure) -> dict[str, Any]:
    """One adventure, verbatim, as the parent reads it (§12.6)."""
    beats = list(Beat.objects.filter(adventure=adventure).select_related("media").order_by("seq"))
    choices = {choice.beat_id: choice for choice in Choice.objects.filter(adventure=adventure)}
    plan = adventure.plan or {}

    return {
        "adventureId": adventure.pk,
        "title": adventure.title,
        "world": str(plan.get("world_name") or ""),
        "place": str(plan.get("place") or ""),
        "status": adventure.status,
        "startedAt": adventure.started_at,
        "completedAt": adventure.completed_at,
        "beats": [_beat(beat, choices.get(beat.pk)) for beat in beats],
    }


def _beat(beat: Beat, choice: Choice | None) -> dict[str, Any]:
    """One beat as it was told, and what the child did about it.

    `narration` is the stored text, unedited. `imageUrl` is the picture that was on
    screen next to it — a parent reading the log should see the same page their
    child saw, and a wall of prose with the illustrations stripped out is a
    different artefact from the one being promised.
    """
    interaction = beat.interaction or {}
    return {
        "seq": beat.seq,
        "narration": beat.narration,
        "emotion": beat.emotion,
        "imageUrl": beat.media.url if beat.media else None,
        "safetyStatus": beat.safety_status,
        "asked": str(interaction.get("prompt_audio") or "") if interaction else "",
        "options": [str(option.get("label") or "") for option in beat.options],
        "answered": _answer(beat, choice),
    }


def _answer(beat: Beat, choice: Choice | None) -> str:
    """What the child did here, in one phrase, or empty if they did not.

    Deliberately the same three readings `memory.prompts._choice_line` uses — said,
    answered, chose — so the transparency log and the consolidation digest never
    disagree about what a tap meant.
    """
    if choice is None:
        return ""
    if isinstance(choice.value, str) and choice.value.strip():
        return f'Said "{choice.value.strip()}"'
    if choice.value is not None:
        return f"Answered {choice.value}"
    option = beat.option(choice.chosen_key) or {}
    if label := str(option.get("label") or "").strip():
        return f'Chose "{label}"'
    return "Tapped to carry on"


def adventures_for(child: Any, *, limit: int) -> list[Adventure]:
    """This child's stories, newest first — the index of the log (§12.10).

    Only adventures with at least one beat: a row created by a story that failed
    before its opening is not something the child experienced, and offering it as a
    readable story would be the log's first empty promise.
    """
    return list(
        Adventure.objects.filter(child=child, beats__isnull=False)
        .distinct()
        .order_by("-started_at")[:limit]
    )
