"""Offline placeholder art (development and CI only).

Not a model and not a fallback for a failed render — it is the `local` image
provider's body, and it exists for two reasons:

1. **The pipeline must be runnable with no keys and no spend.** Hashing, the
   safety scan, storage, CDN URLs and the cache are the parts of Day 3 that have
   to be *correct*; being unable to exercise them without a paid API would mean
   testing them nowhere.
2. **It demonstrates the consistency mechanism honestly.** The creature is drawn
   from the prompt's CHARACTERS section alone and the setting from its SCENE
   section alone, so the same companion is visibly the same across scenes while
   the background changes — which is precisely the property §8.2 asks a real
   image model to hold, made observable without one.

Everything is derived from a digest of those sections, so the same prompt always
yields byte-identical output. No randomness, no clock.

Output is SVG: no image library, no binary encoding, and it renders in an `<img>`
tag (where scripts are inert) exactly like a PNG would.
"""

import hashlib

from apps.media.prompts import CHARACTERS_LABEL, SCENE_LABEL, parse_sections

WIDTH = 1024
HEIGHT = 768

SCENE_PART = "scene"
CHARACTER_PART = "character"

# Warm storybook hues, so placeholder art at least sits inside the §15.9 palette
# rather than looking like a different product.
BODY_COLORS = ("#e2a15a", "#7f9f6b", "#8c7ac0", "#d98f8f", "#5fa8a0", "#c8a24a")
SKY_COLORS = ("#fdf1dc", "#e9f0f7", "#f6e6ef", "#e8f2e4", "#fdeade")
GROUND_COLORS = ("#cfe0b6", "#e6d3ac", "#bcd8d2", "#e3c9b4")
ACCENT_COLORS = ("#e8a33d", "#6d4aad", "#4f9d5d", "#c96a54")


class _Deterministic:
    """A tiny reproducible number source seeded by a digest.

    A linear congruential generator rather than `random`: it must give the same
    sequence on every machine, every Python version, forever — a placeholder
    that drifts would make cached art disagree with freshly drawn art.
    """

    def __init__(self, seed: str) -> None:
        self._state = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16) or 1

    def next(self) -> int:
        self._state = (self._state * 6364136223846793005 + 1442695040888963407) % (2**64)
        return self._state >> 16

    def pick(self, options: tuple[str, ...]) -> str:
        return options[self.next() % len(options)]

    def between(self, low: int, high: int) -> int:
        return low + self.next() % max(1, high - low)


def draw_placeholder_svg(prompt: str) -> bytes:
    """Render one deterministic placeholder illustration for `prompt`."""
    sections = parse_sections(prompt)
    character_key = sections.get(CHARACTERS_LABEL, prompt)
    scene_key = sections.get(SCENE_LABEL, prompt)

    # The two halves are grouped and labelled rather than concatenated, so that
    # "the character did not change when the scene did" is something a test can
    # actually check rather than eyeball.
    scene = _scene(_Deterministic(f"scene:{scene_key}"))
    character = _character(_Deterministic(f"character:{character_key}"))

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img">'
        f'<g data-part="{SCENE_PART}">{scene}</g>'
        f'<g data-part="{CHARACTER_PART}">{character}</g>'
        f"</svg>"
    )
    return svg.encode("utf-8")


def part(svg: bytes, name: str) -> str:
    """Pull one labelled half back out of a placeholder image."""
    text = svg.decode("utf-8")
    opening = f'<g data-part="{name}">'
    start = text.index(opening) + len(opening)
    return text[start : text.index("</g>", start)]


def _scene(rng: _Deterministic) -> str:
    """Background: sky, a light source, hills, and a few standing shapes."""
    sky = rng.pick(SKY_COLORS)
    ground = rng.pick(GROUND_COLORS)
    accent = rng.pick(ACCENT_COLORS)
    horizon = rng.between(430, 520)

    parts = [
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{sky}"/>',
        f'<circle cx="{rng.between(120, 900)}" cy="{rng.between(90, 200)}" '
        f'r="{rng.between(40, 70)}" fill="{accent}" opacity="0.35"/>',
    ]

    # Two overlapping hills give depth without any gradient machinery.
    for offset, opacity in ((0, 1.0), (60, 0.55)):
        y = horizon + offset
        parts.append(
            f'<path d="M0 {y} Q {rng.between(150, 400)} {y - rng.between(60, 130)} '
            f"{rng.between(450, 620)} {y} T {WIDTH} {y - rng.between(0, 40)} "
            f'V {HEIGHT} H 0 Z" fill="{ground}" opacity="{opacity}"/>'
        )

    for _ in range(rng.between(2, 5)):
        x = rng.between(60, WIDTH - 60)
        height = rng.between(90, 190)
        top = horizon - height
        parts.append(
            f'<rect x="{x - 8}" y="{top + height - 40}" width="16" height="60" '
            f'rx="6" fill="#8a6b4f" opacity="0.75"/>'
            f'<ellipse cx="{x}" cy="{top + 30}" rx="{rng.between(45, 80)}" '
            f'ry="{rng.between(40, 70)}" fill="{ground}" opacity="0.9"/>'
        )

    return "".join(parts)


def _character(rng: _Deterministic) -> str:
    """The companion: one silhouette, fully determined by the character section."""
    body = rng.pick(BODY_COLORS)
    belly = rng.pick(("#fdf6e9", "#fff6ef", "#f3f7ec"))
    accent = rng.pick(ACCENT_COLORS)
    cx, cy = WIDTH // 2, 470
    rx, ry = rng.between(110, 145), rng.between(120, 155)

    ear_style = rng.next() % 3
    has_wings = rng.next() % 2 == 0
    has_tail = rng.next() % 2 == 0

    parts: list[str] = []

    if has_wings:
        for direction in (-1, 1):
            wing_x = cx + direction * (rx + 20)
            parts.append(
                f'<ellipse cx="{wing_x}" cy="{cy - 40}" rx="55" ry="80" '
                f'fill="{accent}" opacity="0.5" '
                f'transform="rotate({direction * 20} {wing_x} {cy - 40})"/>'
            )
    if has_tail:
        parts.append(
            f'<path d="M{cx + rx - 10} {cy + 60} q 90 20 110 -60" stroke="{body}" '
            f'stroke-width="26" fill="none" stroke-linecap="round"/>'
        )

    parts.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{body}"/>')
    parts.append(
        f'<ellipse cx="{cx}" cy="{cy + 30}" rx="{rx - 45}" ry="{ry - 55}" fill="{belly}"/>'
    )

    if ear_style == 0:  # round ears
        for direction in (-1, 1):
            parts.append(
                f'<circle cx="{cx + direction * 70}" cy="{cy - ry + 25}" r="34" fill="{body}"/>'
            )
    elif ear_style == 1:  # horns
        for direction in (-1, 1):
            x = cx + direction * 55
            parts.append(
                f'<path d="M{x} {cy - ry + 30} l {direction * 18} -60 l {direction * 26} 42 Z" '
                f'fill="{accent}"/>'
            )
    else:  # a single soft crest
        parts.append(
            f'<path d="M{cx - 60} {cy - ry + 20} q 60 -70 120 0 Z" fill="{accent}" opacity="0.9"/>'
        )

    # Eyes and a smile. Always friendly, always open — a companion that looks
    # unhappy in a placeholder is still a companion a child sees.
    for direction in (-1, 1):
        eye_x = cx + direction * 45
        parts.append(
            f'<circle cx="{eye_x}" cy="{cy - 30}" r="20" fill="#fffdf8"/>'
            f'<circle cx="{eye_x + direction * 4}" cy="{cy - 28}" r="10" fill="#3a2b1f"/>'
        )
    parts.append(
        f'<path d="M{cx - 34} {cy + 20} q 34 34 68 0" stroke="#3a2b1f" stroke-width="7" '
        f'fill="none" stroke-linecap="round"/>'
    )

    return "".join(parts)
