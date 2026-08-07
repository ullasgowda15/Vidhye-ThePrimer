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
