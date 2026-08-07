"""The global art direction (PRODUCT.md §15.9, §15.6).

One style, fixed, appended to every illustration prompt in the product. Locking
it is what makes a thousand generated pictures read as one world rather than a
thousand experiments, and the negative prompts are a *safety* control as much
as an aesthetic one: non-photoreal art avoids uncanny or realistic depictions of
children, which is both safer and more magical for a three-to-seven-year-old.

**Negatives are a parameter, never prose.** `NEGATIVE_TERMS` is what to send in
a provider's `negative_prompt` field. It is deliberately *not* appended to the
positive prompt: diffusion models do not parse negation, so "no text" reaches
the model as the token *text* and "no logos or watermarks" as *watermark* — a
version of this file that concatenated them painted the companion's name into a
render. A provider with no negative-prompt parameter therefore gets nothing
here rather than the opposite of what we meant. See `adapters/image.py`.

This module is the single place the style lives. `STYLE_VERSION` is part of the
hashed string on purpose — editing the art direction changes `style_hash`,
which invalidates every cached illustration in one step rather than leaving two
styles interleaved in one child's storybook.
"""

from django.conf import settings

STYLE_VERSION = "1.0.0"

#: Positive art direction, appended to every image prompt.
STYLE_TOKENS = (
    "soft painterly children's storybook illustration, watercolour and gouache, "
    "warm palette, rounded friendly forms, gentle diffused light, visible paper "
    "grain, picture-book composition with generous negative space, hand-painted "
    "edges, cosy and inviting"
)

#: What must never appear — the value of a provider's `negative_prompt`
#: parameter, never text inside the positive prompt. The first four are the
#: §15.9 list verbatim, and they are a safety control: "no realistic human
#: children" and "no scary imagery" are not style preferences.
NEGATIVE_TERMS = (
    "no photorealism",
    "no text",
    "no realistic human children",
    "no scary imagery",
    "no photographic rendering",
    "no 3d render",
    "no harsh shadows",
    "no weapons",
    "no blood",
    "no logos or watermarks",
)

NEGATIVE_PROMPT = ", ".join(NEGATIVE_TERMS)


def style_tokens() -> str:
    """The exact art-direction string that goes into the prompt."""
    return f"[style v{STYLE_VERSION}] {STYLE_TOKENS}"


def style_signature() -> str:
    """The string hashed into `style_hash`. Not the same as the prompt text.

    The model id must never reach the prompt — a text-to-image model will
    happily paint "flux-1-schnell" into the corner — but it *must* reach the
    cache key. Without it, changing `IMAGE_MODEL` serves every existing child
    their old art from the previous model forever, which is the exact
    interleaving `STYLE_VERSION` exists to prevent.
    """
    return f"{style_tokens()} [model {settings.IMAGE_MODEL}]"


def negative_prompt() -> str:
    """The negatives, for a provider's `negative_prompt` parameter only."""
    return NEGATIVE_PROMPT
