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

#: Said when the beat follows a choice. The word doing the work is **name**: a
#: model asked only to "continue" writes a beat that would have followed either
#: option, and a consequence the child cannot recognise as theirs is not a
#: consequence at all (§8.5) — agency is the whole point of asking.
CONSEQUENCE_RULE = """\
The child has just chosen, and the DATA below says what they picked. This beat \
is the consequence. Name what they chose in the narration — the place they went, \
the creature they helped, the thing they tried — so it is unmistakably *their* \
choice that changed the world. Show the change in scene.illustration_prompt too, \
not only in the words. A beat that would have read the same after the other \
option is the one thing this must not be.\
"""

#: Said when the child needed a hand. §8.5 in one paragraph: struggle is
#: scaffolded, never punished, and the companion models trying rather than
#: grading. Written as what to *do*, because "do not say wrong" on its own leaves
#: a model with nothing to write.
SCAFFOLD_RULE = """\
The child took a few tries, or the companion helped them. Open this beat by \
doing it *together* and getting there — "Let's try it together", counting along, \
a hand held out. Celebrate that they kept going. Do not mention a mistake, do not \
say wrong or nearly or almost, and do not make this beat easier-looking than the \
last one in a way the child would notice. Nothing was lost and nothing is behind.\
"""

#: Said on every beat that is not the last one. The 2-3 bound is §14.5's "one
#: decision at a time, max 2-3 options"; the pictures are §14.4, because a text
#: list excludes the entire audience.
CHOICE_RULE = """\
End this beat by giving the child something to do. Prefer interaction.type \
"choose" with two or three options in interaction.choices — each one a different \
thing that happens next, each leading somewhere good. There are no wrong options \
and no option that ends the story badly. Give every option a short spoken label \
and an icon_prompt describing one simple picture of it: a place, an object, an \
action, no characters and no writing in the image.\
"""

#: Said instead of `CHOICE_RULE` on the one beat that asks the child to count
#: (§10.3's objective, `orchestrator.asks_to_count`).
#:
#: This exists because `CHOICE_RULE` says "prefer choose", and a model given that
#: on every beat picks `choose` every time — which left the numeracy objective
#: riding along in the prompt as data that nothing ever acted on, and left §8.5's
#: no-fail-states guarantee with nothing that *can* be answered wrongly and so no
#: way to be exercised. A `choose` has no wrong answer; that is the point of it,
#: and it is also why it cannot be the only thing a beat ever asks.
#:
#: The count is small and visible on purpose: the child answers by looking at the
#: picture, not by knowing arithmetic, and the illustration has to actually show
#: the things or the question is unanswerable.
COUNT_RULE = """\
End this beat by asking the child to count something. Set interaction.type to \
"count", set interaction.expected to how many there are as a whole number of \
three or fewer, and set interaction.choices to null — this beat asks for a \
number, not a pick.

The things being counted must be in the scene and named in \
scene.illustration_prompt, so the child can answer by looking at the picture. \
interaction.prompt_audio is the companion asking how many there are, in one \
short sentence.

There is no wrong answer here. Do not hint at what happens if they miscount, do \
not praise the right number in advance, and do not make the counting sound like \
a test.\
"""

#: Said only on the last beat (§8.8). The hook is the reason a child asks to come
#: back, and it is the one thing a model will not produce unless asked.
FINAL_BEAT_RULE = """\
This is the last beat of the sitting. Land the story: the problem is solved, \
everyone is safe, and the companion says something warm to the child by name. \
Then plant one small hook for tomorrow — something the companion has found or \
noticed that they could go and see next time. Nothing is left unresolved and \
nothing is frightening. Set interaction.type to "none" and interaction.choices to \
null: the child is not being asked for anything here, they are being said \
goodnight to.\
"""


def beat_request(
    *,
    seq: int,
    beat_id: str,
    is_first: bool,
    is_final: bool = False,
    ask_to_count: bool = False,
    stage: str = "",
    prev_choice: Mapping[str, Any] | None = None,
    characters: Sequence[str] = (),
) -> str:
    """The user turn asking for one beat.

    `stage` is the line from the plan skeleton this beat is meant to land, passed
    explicitly rather than left for the model to infer from the plan: "which step
    are we on" is the one thing about the plan that a stateless call cannot know.

    `is_final` is the same kind of fact and matters more. §8.8 requires the last
    beat of every sitting to be warm and safe, and a stateless call has no way to
    know it is writing the last one — a model told only "beat six of six" will
    happily leave the child mid-problem.
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
        lines.append(CONSEQUENCE_RULE)
        if prev_choice.get("assisted") or prev_choice.get("retries"):
            lines.append(SCAFFOLD_RULE)

    if is_final:
        lines.append(FINAL_BEAT_RULE)
    else:
        lines.append(COUNT_RULE if ask_to_count else CHOICE_RULE)

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
