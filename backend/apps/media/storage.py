"""Object storage for generated media (PRODUCT.md §16.6, §16.7).

Blobs go to S3-compatible storage and are served from a CDN; the app writes
them once and never reads them back. Postgres holds only the pointer.

Two decisions are worth stating:

**Content-addressed keys.** The object name is a digest of its own bytes, so an
asset can be written twice without producing two objects, and a stored URL can
be cached at the edge forever — different bytes are always a different key.

**The child id is in the path.** Not for access control (these URLs are public,
CDN-served, and contain no personal data), but so that "delete everything you
hold about my child" (§13.8) is a prefix delete rather than a table scan.
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


def object_key(child_id: str, data: bytes, content_type: str, prefix: str = ASSET_PREFIX) -> str:
    return f"{prefix}/{child_id}/{content_digest(data)}{extension_for(content_type)}"


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


def put(child_id: str, data: bytes, *, content_type: str, prefix: str = ASSET_PREFIX) -> str:
    """Store bytes and return the URL to serve them from.

    Idempotent by construction: the key is derived from the content, so writing
    the same asset twice overwrites one object with identical bytes.
    """
    key = object_key(child_id, data, content_type, prefix=prefix)
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
