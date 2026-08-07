"""Image/audio safety scan (PRODUCT.md §13.8, §13.2).

**This is a seam, not a classifier.** Day 7 hardens it into a real moderation
call; what must be true *today* is that the call site exists and is
unavoidable — every byte that reaches storage passes through `scan()` first,
and the verdict is written onto the `Media` row. Adding the real model later is
then replacing the body of one function, not threading a new step through the
pipeline.

What the stub actually enforces is the structural half of the check, which is
worth having on its own: an asset that is empty, absurdly large, or not an
image/audio file at all is a provider failure or a hostile response, and none of
those should ever be stored, let alone shown to a child.

# TODO: harden Day 7 — replace `_classify` with a provider moderation call
# (or a local NSFW/violence classifier) and keep the same Scan contract.
"""

import logging
from dataclasses import dataclass

from django.conf import settings

from apps.media.models import SafetyStatus

logger = logging.getLogger(__name__)

#: Magic-byte prefixes we are willing to store, mapped to their media type.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # refined below — RIFF also fronts WAV
    (b"<svg", "image/svg+xml"),
    (b"<?xml", "image/svg+xml"),
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"),
    (b"\xff\xf3", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
)


@dataclass(frozen=True, slots=True)
class Scan:
    """The verdict on one asset."""

    status: str
    content_type: str = "application/octet-stream"
    reason: str = ""

    @property
    def is_safe(self) -> bool:
        return self.status == SafetyStatus.PASSED


def sniff_content_type(data: bytes) -> str:
    """Best-effort media type from the leading bytes.

    Sniffed rather than trusted from the adapter: the content type decides the
    stored object's extension and the `Content-Type` a child's browser is
    served, so it must describe the bytes we actually hold.
    """
    head = data[:16]
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    for signature, content_type in _SIGNATURES:
        if head.startswith(signature):
            return content_type
    return "application/octet-stream"


def scan(data: bytes) -> Scan:
    """Classify one generated asset before it is stored.

    Never raises: a scan that blows up must not become an unhandled exception on
    the story path. It returns `BLOCKED` and lets the caller decide, which for
    the child-facing pipeline means falling back rather than showing an error.
    """
    try:
        return _classify(data)
    # Deliberately total: see the docstring — a scan that raises must not become
    # an unhandled exception on the story path.
    except Exception:
        logger.exception("Image safety scan failed; blocking the asset.")
        return Scan(status=SafetyStatus.BLOCKED, reason="scan_error")


def _classify(data: bytes) -> Scan:
    if not data:
        return Scan(status=SafetyStatus.BLOCKED, reason="empty_response")

    limit = settings.MEDIA_MAX_BYTES
    if len(data) > limit:
        return Scan(
            status=SafetyStatus.BLOCKED,
            reason=f"oversize:{len(data)}>{limit}",
        )

    content_type = sniff_content_type(data)
    if content_type == "application/octet-stream":
        # Not recognisably a picture or a sound. Flagged rather than passed:
        # unknown content is exactly what a human should look at.
        return Scan(
            status=SafetyStatus.FLAGGED,
            content_type=content_type,
            reason="unrecognised_format",
        )

    return Scan(status=SafetyStatus.PASSED, content_type=content_type)
