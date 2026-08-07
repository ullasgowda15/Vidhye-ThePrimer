"""The global art direction (PRODUCT.md §15.9, §15.6).

One style, fixed, appended to every illustration prompt in the product. Locking
it is what makes a thousand generated pictures read as one world rather than a
thousand experiments, and the negative prompts are a *safety* control as much
as an aesthetic one: non-photoreal art avoids uncanny or realistic depictions of
children, which is both safer and more magical for a three-to-seven-year-old.

This module is the single place the style lives. `STYLE_VERSION` is part of the
hashed string on purpose — editing the art direction changes `style_hash`,
which invalidates every cached illustration in one step rather than leaving two
styles interleaved in one child's storybook.
"""

STYLE_VERSION = "1.0.0"

#: Positive art direction, appended to every image prompt.
STYLE_TOKENS = (
    "soft painterly children's storybook illustration, watercolour and gouache, "
    "warm palette, rounded friendly forms, gentle diffused light, visible paper "
    "grain, picture-book composition with generous negative space, hand-painted "
    "edges, cosy and inviting"
)

#: What must never appear. The first four are the §15.9 list verbatim.
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
    """The exact art-direction string that goes into the prompt and the hash."""
    return f"[style v{STYLE_VERSION}] {STYLE_TOKENS}"


def negative_prompt() -> str:
    return NEGATIVE_PROMPT
