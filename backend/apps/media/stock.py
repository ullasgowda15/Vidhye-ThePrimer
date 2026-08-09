"""Safe stock illustrations (PRODUCT.md §13.2's `IMGFB`, §13.8).

§13.2's image branch ends `G3 -->|fail| IMGFB[Safe stock illustration]`. This is
`IMGFB`.

**They are files in the repository, drawn by hand.** Not generated, not cached
from an earlier render, not a placeholder from the offline adapter — because the
one picture a child sees when the image gate has *just refused something* must not
have come from the pipeline that produced the refused thing. They are SVG, they
are a few kilobytes each, they are reviewed like code, and a change to one is a
diff a person reads.

**They are ordinary pictures, not error states.** A wide calm scene in the
product's own palette, with nothing happening in it. `StoryPage` renders it as the
beat's illustration and nothing on screen says anything went wrong; a child sees a
quieter picture (§13).

**They go through the real pipeline.** Stored content-addressed, given a `Media`
row per child, scanned like everything else. That means the second substitution
for a child is a cache hit rather than a second write, and it means the substitute
is subject to the same `is_servable` rule as a generated asset — a stock file that
somehow failed a scan would be withheld rather than trusted for being ours.

**No companion in them.** A substitution can land on any beat of any story, and a
stock picture with a character in it would be a picture of the wrong character
doing nothing, which is worse than a landscape (§8.2).
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from apps.media import service as media_service
from apps.media.hashing import hashes_for
from apps.media.models import Media, MediaType

if TYPE_CHECKING:
    from apps.profiles.models import Child

logger = logging.getLogger(__name__)

STOCK_DIR = Path(__file__).resolve().parent / "stock"

#: The pool, in a fixed order. Named rather than globbed so that adding a file to
#: the directory is not the same act as putting it in front of a child.
STOCK_NAMES: tuple[str, ...] = ("meadow", "riverbank", "treeshade")

#: The provider label written onto the `Media` row. Distinct from every real
#: provider so "how often did we substitute" is one query (§13.11), and so stock
#: art can never be mistaken for a render in a cost report (§22.4).
PROVIDER = "stock"

#: Version this when the artwork changes. It is part of the hashed style string,
#: so a redraw invalidates every cached row rather than leaving two generations of
#: stock art interleaved — the same reasoning as `media.style.STYLE_VERSION`.
STOCK_VERSION = "1.0.0"


class StockUnavailable(Exception):
    """The stock file could not be read. The caller falls back to no picture."""


@lru_cache(maxsize=len(STOCK_NAMES))
def _read(name: str) -> bytes:
    path = STOCK_DIR / f"{name}.svg"
    try:
        return path.read_bytes()
    except OSError as exc:
        raise StockUnavailable(f"Stock illustration '{name}' is unreadable: {exc}") from exc


def choose(seed: str) -> str:
    """Which stock picture, deterministically, from anything stable.

    Deterministic so a replayed beat shows the same picture it showed the first
    time — a substituted illustration that changed between two loads of the same
    beat would be a stranger inconsistency than the substitution itself.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return STOCK_NAMES[int(digest[:8], 16) % len(STOCK_NAMES)]


def safe_illustration(child: Child, *, seed: str = "") -> Media:
    """A pre-vetted picture for this child, stored and servable.

    Raises `StockUnavailable` if the file cannot be read — the one failure this
    module can have, and the caller (`orchestrator.illustrate_beat`) turns it into
    a beat with no picture, which is already a fully playable beat (§4.9).
    """
    name = choose(seed or "default")
    data = _read(name)

    style, character, scene = hashes_for(
        f"stock v{STOCK_VERSION}",
        # No cast, ever — see the module docstring.
        "stock:no-characters",
        name,
    )
    media = media_service.get_or_generate_stock(
        child=child,
        style_hash=style,
        character_hash=character,
        scene_hash=scene,
        data=data,
        provider_name=PROVIDER,
    )
    logger.info("media.stock_served child=%s name=%s media=%s", child.id, name, media.id)
    return media


def is_stock(media: Media | None) -> bool:
    """Was this asset a substitution? Read by the incident detail and by tests."""
    return media is not None and media.provider == PROVIDER and media.type == MediaType.IMAGE
