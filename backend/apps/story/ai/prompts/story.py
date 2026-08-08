"""The two requests the story orchestrator makes (PRODUCT.md §8.1, §19.1).

These are **user-turn** text only. Everything that makes a generation safe — the
Primer's identity, the per-child safety policy, the companion's canonical state,
the learning directive, the output contract — is assembled by
`apps.safety.prompting.assembler` and travels in the system layer. Nothing here
may restate a rule, because a rule written in two places is a rule that will
disagree with itself.

Both functions return plain strings and touch nothing: the same adventure asked
for the same beat produces the same bytes, which is what makes the whole prompt
assembly testable without a model.
"""

from collections.abc import Mapping, Sequence
from typing import Any

#: Asked of the *plan* call. Short on purpose — a plan detailed enough to be a
#: script would make the child's choices cosmetic (§8.5).
PLAN_REQUEST = """\
Plan today's adventure for this child.

Give it a name they would use themselves, then lay out five to seven one-line \
stages in order: an opening hook, a challenge, a climax, and a warm resolution. \
Each stage is one sentence about what *happens*, not how it is narrated.

Then name two or three branch themes — different directions the child's choices \
could take the middle of the story. They must all lead somewhere good.

Build it around what the child loves. The learning objective belongs inside the \
plot, never announced.\
"""


def beat_request(
    *,
    seq: int,
    beat_id: str,
    is_first: bool,
    stage: str = "",
    prev_choice: Mapping[str, Any] | None = None,
    characters: Sequence[str] = (),
) -> str:
    """The user turn asking for one beat.

    `stage` is the line from the plan skeleton this beat is meant to land, passed
    explicitly rather than left for the model to infer from the plan: "which step
    are we on" is the one thing about the plan that a stateless call cannot know.
    """
    lines = [
        f'Write beat {seq} of this adventure. Use "{beat_id}" as its beat_id.',
    ]

    if stage:
        lines.append(f"This beat covers this stage of the plan: {stage}")

    if is_first:
        lines.append(
            "This is the opening. Arrive somewhere: establish the place, let the "
            "companion greet the child by name, and end on something the child "
            "wants to do next."
        )
    elif prev_choice:
        lines.append(
            "Continue from what the child just did. Their choice must visibly "
            "change something in this beat."
        )

    if characters:
        lines.append(
            "Characters whose canonical appearance we hold, and whose names you "
            "may use: " + ", ".join(characters) + ". Any other character in the "
            "scene must be described in scene.illustration_prompt rather than "
            "named as if we already know them."
        )

    lines.append(
        "scene.illustration_prompt describes only what is *in the picture* — "
        "place, light, what each character is doing. Do not name a style; the "
        "art direction is applied downstream."
    )
    # Said out loud, one clip, narration first. Models reach for "restate the
    # scene, then ask" here, and the result is a companion who tells the child the
    # whole beat twice in a row — so what this field is *not* has to be said.
    lines.append(
        "interaction.prompt_audio is spoken straight after the narration, in the "
        "same breath. Make it the question alone — one short sentence, nothing the "
        "narration already said. If the narration has already asked, use null."
    )
    return "\n\n".join(lines)
