"""Image/audio safety scan (PRODUCT.md §13.8, §13.2's G3).

Two checks, in this order, and every byte that reaches storage passes through
both:

**Structural.** Empty, absurdly large, or not an image or a sound at all. These
are provider failures and hostile responses, and none of them should be stored,
let alone shown to a child. Cheap, total, and it runs everywhere.

**Moderation.** A vision model reads the picture (`apps.media.moderation`) and
says whether a three-year-old may see it. This is the half Day 3 left as a
`TODO` and Day 7 made real. It runs when a provider is configured; when one is
not, the scan says `moderation=absent` in its log line and the reason on the
verdict rather than quietly reporting a check it did not perform.

The verdict lands on the `Media` row, and `Media.is_servable` is what every
call site reads — so "flagged also means do not serve" is stated once. Bytes
that fail are never written and never get a URL (`media.service`), so there is
no window in which an unscanned asset is reachable.

What happens *after* a refusal is not this module's business and is worth
knowing anyway: `orchestrator.illustrate_beat` substitutes a pre-vetted stock
illustration (`apps.media.stock`), so a blocked picture is a different picture
and never an error on a child's screen (§13).
"""

import logging
from dataclasses import dataclass

from django.conf import settings

from apps.media import moderation
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
    #: Did the vision moderator actually run? `False` means the structural check
    #: stood alone. Carried on the verdict rather than left to the log, so a
    #: caller — and a test — can assert on it (§13.8).
    moderated: bool = False

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
        # unknown content is exactly what a human should look at. Returned before
        # the moderator, because bytes we cannot even identify are not something
        # to spend a vision call on.
        return Scan(
            status=SafetyStatus.FLAGGED,
            content_type=content_type,
            reason="unrecognised_format",
        )

    status, reason = moderation.classify(data, content_type)

    if status == SafetyStatus.PENDING:
        # The gate did not run — no configured moderator, or a content type with
        # no pixels. The structural verdict stands and the gap is stated, on the
        # verdict and in the log, rather than dressed up as a clean scan.
        logger.info(
            "media.scan content_type=%s bytes=%d moderation=absent reason=%s",
            content_type,
            len(data),
            reason,
        )
        return Scan(status=SafetyStatus.PASSED, content_type=content_type, moderated=False)

    if status != SafetyStatus.PASSED:
        logger.error(
            "media.scan_refused content_type=%s bytes=%d status=%s reason=%s",
            content_type,
            len(data),
            status,
            reason,
        )
        return Scan(status=status, content_type=content_type, reason=reason, moderated=True)

    logger.info(
        "media.scan content_type=%s bytes=%d moderation=passed", content_type, len(data)
    )
    return Scan(status=SafetyStatus.PASSED, content_type=content_type, moderated=True)
