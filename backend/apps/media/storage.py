"""Object storage for generated media (PRODUCT.md §16.6, §16.7).

Blobs go to S3-compatible storage and are served from a CDN; the app writes
them once and never reads them back. Postgres holds only the pointer.

Two decisions are worth stating:

**Content-addressed keys.** The object name is a digest of its own bytes, so an
asset can be written twice without producing two objects, and a stored URL can
be cached at the edge forever — different bytes are always a different key.

**No child id is in the path.** It used to be, so that "delete everything you
hold about my child" (§13.8/§17.5) could be a prefix delete. That was wrong the
moment the pipeline started reusing one child's render for another: the second
family's storybook then pointed at an object under the first family's prefix,
and honouring the first family's deletion request would have quietly broken the
second's pictures. Ownership lives on the `Media` row and only there; the
digest already makes the key unique, and identical bytes are now stored once
across the whole product rather than once per child.

Deletion is therefore `delete_orphans` — remove the objects no surviving row
points at — which `apps.media.service.purge_media` calls after deleting rows.
"""

import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages

from apps.media.hashing import content_digest

logger = logging.getLogger(__name__)

MEDIA_STORAGE_ALIAS = "media"

#: Key prefix inside the bucket. One namespace for everything the pipeline
#: generates, so a bucket shared with anything else stays legible.
ASSET_PREFIX = "assets"

_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
}


def media_storage() -> Storage:
    """The configured media backend — S3-compatible in staging/prod, disk in dev."""
    return storages[MEDIA_STORAGE_ALIAS]


def extension_for(content_type: str) -> str:
    return _EXTENSIONS.get(content_type, ".bin")


def object_key(data: bytes, content_type: str, prefix: str = ASSET_PREFIX) -> str:
    """The object's name: its own digest. Nothing about who asked for it."""
    return f"{prefix}/{content_digest(data)}{extension_for(content_type)}"


def public_url(key: str) -> str:
    """The URL a child's device fetches.

    When a CDN is configured the URL is built by joining, not by asking the
    backend: the whole point of the CDN is that the origin is not in the path,
    and S3's own `url()` would hand back a bucket hostname (or, with signing on,
    a URL that expires and cannot be cached).
    """
    if settings.MEDIA_CDN_BASE_URL:
        return f"{settings.MEDIA_CDN_BASE_URL}/{key.lstrip('/')}"
    return media_storage().url(key)


def put(data: bytes, *, content_type: str, prefix: str = ASSET_PREFIX) -> str:
    """Store bytes and return the URL to serve them from.

    Idempotent by construction: the key is derived from the content, so writing
    the same asset twice overwrites one object with identical bytes.
    """
    key = object_key(data, content_type, prefix=prefix)
    storage = media_storage()

    if not storage.exists(key):
        storage.save(key, ContentFile(data))

    url = public_url(key)
    logger.info(
        "media.stored key=%s bytes=%d content_type=%s",
        key,
        len(data),
        content_type,
    )
    return url


def key_for_url(url: str) -> str:
    """The storage key behind a stored URL, or `""` if it is not one of ours.

    The inverse of `public_url`, and it has to cope with all three shapes a
    stored URL can have: a CDN join, a development `/media/...` path, and a
    bucket URL from the backend's own `url()`. Anything it cannot place is left
    alone — a deletion path that guesses is worse than one that skips.
    """
    candidate = url.split("?", 1)[0].split("#", 1)[0]

    if settings.MEDIA_CDN_BASE_URL and candidate.startswith(settings.MEDIA_CDN_BASE_URL):
        return candidate[len(settings.MEDIA_CDN_BASE_URL) :].lstrip("/")

    marker = f"/{ASSET_PREFIX}/"
    if marker in candidate:
        # Covers both `/media/assets/…` and `https://bucket.host/assets/…`
        # without needing to know which one this deployment produces.
        return candidate[candidate.index(marker) + 1 :]

    if candidate.startswith(settings.MEDIA_URL):
        return candidate.removeprefix(settings.MEDIA_URL).lstrip("/")

    return ""


def delete_orphans(urls: set[str]) -> int:
    """Delete the objects behind `urls`. Returns how many were removed.

    Callers pass only URLs no `Media` row points at any more — objects are
    shared across children by design, so deleting one still referenced would
    blank another family's storybook. Never raises: a storage backend that
    cannot delete must not turn a completed row deletion into a 500.
    """
    storage = media_storage()
    removed = 0

    for url in urls:
        key = key_for_url(url)
        if not key:
            logger.warning("media.delete_skipped url=%s reason=unrecognised", url)
            continue
        try:
            if storage.exists(key):
                storage.delete(key)
                removed += 1
        except Exception:
            logger.exception("media.delete_failed key=%s", key)

    if removed:
        logger.info("media.deleted objects=%d", removed)
    return removed
