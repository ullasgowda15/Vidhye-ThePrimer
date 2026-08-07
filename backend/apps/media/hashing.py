"""Cache keys for generated media (PRODUCT.md §16.6, §5.8).

An illustration is a pure function of three inputs — the global art direction,
the characters present with their canonical descriptors, and the scene — so
those three, hashed, *are* the cache key. Nothing else may enter: not the
child, not the time, not a request id, or identical pictures would be paid for
twice.

Truncated to 16 hex characters (64 bits). At the scale of one product's
illustration set a collision is not a realistic event, and short keys keep the
composite index small and the debug output readable.
"""

import hashlib

HASH_LENGTH = 16


def sha16(value: str) -> str:
    """A stable, short, deterministic digest of a string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def content_digest(data: bytes) -> str:
    """The digest of raw bytes, used to name objects in storage.

    Content-addressed keys mean identical bytes land on the same object, so the
    same picture is stored once however many rows point at it, and a rewrite of
    the same asset is a no-op rather than a duplicate.
    """
    return hashlib.sha256(data).hexdigest()[:32]


def hashes_for(
    style_tokens: str,
    character_descriptor: str,
    scene_desc: str,
) -> tuple[str, str, str]:
    """The `(style, character, scene)` triple for one illustration.

    Callers pass the *rendered* strings, not the objects they came from: what
    is hashed must be exactly what is sent to the model, or a change in prompt
    text would silently serve the old picture.
    """
    return sha16(style_tokens), sha16(character_descriptor), sha16(scene_desc)
