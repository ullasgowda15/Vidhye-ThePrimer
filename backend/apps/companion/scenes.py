"""The companion's reference scenes and its first words (PRODUCT.md §9.6, §4.3).

The reference set is generated **once** and reused forever: a portrait that is
the anchor for every later illustration, and the five canonical expressions
§9.6 names. Rendering them once is what makes later stories fast, cheap, and —
most importantly — consistent, because a beat that needs a proud companion
reuses the proud companion rather than asking a model to invent one.

Scene text lives here as plain constants rather than being generated, for the
same reason the art direction does: these strings are hashed into the cache key,
so they must be stable across processes and releases. A generated scene
description would silently invalidate every child's reference set.
"""

import re

from apps.companion.models import CANONICAL_EXPRESSIONS, PORTRAIT

#: label -> the scene half of the illustration prompt. The character half comes
#: from the companion's canonical descriptor (§8.2), never from here.
REFERENCE_SCENES: dict[str, str] = {
    PORTRAIT: (
        "a calm three-quarter portrait, centred, looking gently at the viewer, "
        "plain softly-washed background, full body visible, relaxed friendly posture"
    ),
    "happy": (
        "beaming with delight, eyes crinkled shut, mid-bounce, plain soft background, "
        "full body visible"
    ),
    "curious": (
        "head tilted, leaning forward with wide interested eyes, one hand raised as if "
        "about to point, plain soft background, full body visible"
    ),
    "proud": (
        "standing tall with chest out and a small pleased smile, hands on hips, "
        "plain soft background, full body visible"
    ),
    "gentle_concern": (
        "soft worried eyebrows and a kind small frown, leaning in as if listening "
        "closely, one hand reaching out gently, plain soft background, full body visible"
    ),
    "sleepy": (
        "mid-yawn with drooping eyelids, curled slightly, plain soft background, "
        "full body visible"
    ),
}

#: The order they are generated in. Portrait first: it is the one asset the
#: greeting scene cannot open without, so a run that is cut short still leaves a
#: child with a face to meet.
REFERENCE_ORDER: tuple[str, ...] = (PORTRAIT, *CANONICAL_EXPRESSIONS)

#: The pose half of each canonical expression, without the studio background
#: (§9.6). `REFERENCE_SCENES` renders each expression *as a reference asset* —
#: plain background, full body — and these are the same faces asked for inside an
#: ordinary story scene, where the setting comes from the beat instead.
#:
#: Word-for-word the opening clause of the matching reference scene, and that is
#: load-bearing rather than tidy: the pre-rendered asset is the anchor a beat is
#: meant to match (§9.6), so asking for it in different words would be asking a
#: text-only model for a different face than the one we hold.
EXPRESSION_CUES: dict[str, str] = {
    "happy": "beaming with delight, eyes crinkled shut, mid-bounce",
    "curious": "head tilted, leaning forward with wide interested eyes, one hand raised as if "
    "about to point",
    "proud": "standing tall with chest out and a small pleased smile, hands on hips",
    "gentle_concern": "soft worried eyebrows and a kind small frown, leaning in as if listening "
    "closely, one hand reaching out gently",
    "sleepy": "mid-yawn with drooping eyelids, curled slightly",
}

#: Words a beat's free-text `emotion` may use for each canonical expression.
#:
#: A beat's emotion is not drawn from a closed set — it comes back as things like
#: `playful_curious` or `warm_proud`, because it is a note about tone for the
#: narration rather than an instruction to the illustrator. Mapping it onto the
#: five faces we actually hold is therefore a lookup with synonyms, and anything
#: unrecognised maps to nothing at all rather than to a guess: the companion's
#: default face is better than the wrong one.
EXPRESSION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "happy": (
        "happy",
        "happily",
        "joy",
        "joyful",
        "delight",
        "delighted",
        "cheerful",
        "glad",
        "merry",
        "playful",
        "excited",
        "gleeful",
    ),
    "curious": (
        "curious",
        "curiosity",
        "wondering",
        "wonder",
        "inquisitive",
        "intrigued",
        "puzzled",
        "interested",
    ),
    "proud": ("proud", "pride", "triumphant", "accomplished", "brave", "determined"),
    "gentle_concern": (
        "concern",
        "concerned",
        "worried",
        "worry",
        "anxious",
        "nervous",
        "uncertain",
        "cautious",
        "tender",
        "sympathetic",
    ),
    # Explicitly drowsy words only. `calm`, `quiet`, `peaceful` and `restful`
    # used to live here and were a face bug: the only sleepy asset we hold is a
    # *mid-yawn*, so `joyful_calm` — a real emotion the model returns — asked the
    # illustrator for a companion yawning through a happy moment. A calm joy and
    # a sleepy calm are different faces and only one of them was ever right.
    # Losing them costs nothing: an emotion that matches no drowsy word now gets
    # no expression clause, which is this module's documented safe outcome, and
    # `joyful_calm` falls through to the `joyful` it actually is.
    "sleepy": (
        "sleepy",
        "sleep",
        "sleeping",
        "drowsy",
        "tired",
        "yawning",
        "yawn",
    ),
}

_EXPRESSION_BY_WORD: dict[str, str] = {
    word: label for label, words in EXPRESSION_SYNONYMS.items() for word in words
}


def canonical_expression(emotion: str) -> str | None:
    """Which of the five pre-rendered faces this beat's emotion asks for, if any.

    Scanned **right to left**, because a compound emotion puts its head last:
    `playful_curious` is a kind of curious, not a kind of playful, and a
    left-to-right scan would have drawn the wrong one of the two faces we hold.

    Returns None for anything unrecognised, and that is the common, correct
    outcome for an emotion like `warm_welcoming`. The companion then gets no
    expression clause at all and the illustration prompt describes the moment on
    its own — which is what happened for every beat before this existed.
    """
    words = [word for word in re.split(r"[^a-z]+", emotion.casefold()) if word]
    for word in reversed(words):
        if label := _EXPRESSION_BY_WORD.get(word):
            return label
    return None


#: Scenes for `manage.py render_companion --scenes N`. Ordinary story settings,
#: deliberately unlike the reference scenes, so "is this the same character?"
#: is answered by looking at three different pictures rather than three
#: variations of a portrait.
DEMO_SCENES: tuple[str, ...] = (
    "standing at the edge of a mossy forest clearing at dawn, mist between the trees, "
    "looking back over one shoulder",
    "sitting on a smooth rock beside a shallow stream, dangling one foot in the water, "
    "afternoon light through leaves",
    "curled up under a blanket of stars on a grassy hill, a lantern glowing nearby, "
    "night sky full of soft constellations",
    "peeking around the corner of a tall wooden door in a warm lamplit hallway",
    "walking along a windy beach at sunset, footprints trailing behind, gulls high above",
)


def greeting_text(companion_name: str, child_name: str) -> str:
    """The companion's first words (§4.3).

    Fixed text, not generated. Three reasons: it is the one line in the product
    that must never be surprising, it is the same every time so it caches to a
    single TTS call per child, and it does the two jobs the imprint moment
    exists for — it uses the child's name, and it is curious about them.
    """
    return (
        f"Hi {child_name}! I'm {companion_name}. "
        f"I've been waiting to meet you. "
        f"I'm so glad you're here."
    )
