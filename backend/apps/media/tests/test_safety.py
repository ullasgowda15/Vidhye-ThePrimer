"""The image safety seam (PRODUCT.md §13.8).

The classifier is a Day 7 job. What is tested here is the contract Day 7 will
harden behind: the scan is total (it never raises), it names a content type, and
anything it does not recognise is withheld rather than waved through.
"""

from apps.media.models import SafetyStatus
from apps.media.safety import Scan, scan, sniff_content_type

PNG = b"\x89PNG\r\n\x1a\n" + b"..."
JPEG = b"\xff\xd8\xff\xe0" + b"..."
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"..."
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"..."
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'


def test_recognised_images_pass_and_carry_a_content_type() -> None:
    for data, expected in ((PNG, "image/png"), (JPEG, "image/jpeg"), (SVG, "image/svg+xml")):
        verdict = scan(data)
        assert verdict.status == SafetyStatus.PASSED
        assert verdict.content_type == expected


def test_riff_containers_are_told_apart() -> None:
    # WAV and WEBP share a magic prefix; storing audio with a `.webp` extension
    # would make it unplayable in the browser.
    assert sniff_content_type(WAV) == "audio/wav"
    assert sniff_content_type(WEBP) == "image/webp"


def test_an_empty_response_is_blocked() -> None:
    assert scan(b"").status == SafetyStatus.BLOCKED


def test_an_oversize_asset_is_blocked(settings) -> None:
    settings.MEDIA_MAX_BYTES = 8
    assert scan(PNG + b"x" * 64).status == SafetyStatus.BLOCKED


def test_unrecognised_content_is_flagged_not_passed() -> None:
    # Withheld by default: `is_servable` only accepts PASSED, so a flagged asset
    # never reaches a child even though it is kept for review.
    verdict = scan(b"this is not an image at all")
    assert verdict.status == SafetyStatus.FLAGGED
    assert not verdict.is_safe


def test_a_scan_that_explodes_blocks_rather_than_raises(monkeypatch) -> None:
    def boom(data: bytes) -> Scan:
        raise RuntimeError("classifier is down")

    monkeypatch.setattr("apps.media.safety._classify", boom)
    verdict = scan(PNG)

    assert verdict.status == SafetyStatus.BLOCKED
    assert verdict.reason == "scan_error"
