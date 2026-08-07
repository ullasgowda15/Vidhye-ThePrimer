"""Image adapters (PRODUCT.md §16.6, §5.8).

One interface — `generate(prompt) -> bytes` — and a provider name in
configuration. The pipeline above never knows which vendor drew the picture; the
`Media` row records it so any asset can be traced back to its source.

Providers:

* ``cloudflare`` — FLUX on Cloudflare Workers AI, the real provider. §5.8 names
  Flux/SDXL/Imagen; this is the one that is implemented.
* ``local`` — a deterministic offline placeholder that draws flat vector art
  from the prompt's own CHARACTERS and SCENE sections. Not a model: it exists so
  the pipeline (hashing, scan, storage, CDN URLs, cache) is exercisable and
  testable with no key and no spend, and so `render_companion` demonstrates
  character consistency across scenes without burning credits.
* ``together`` / ``openai`` / ``google`` — declared, typed, unimplemented
  (§19.3's fallback seam). They raise rather than silently not existing.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Protocol, runtime_checkable

from django.conf import settings

from apps.media.exceptions import MediaConfigurationError, MediaProviderError
from apps.media.placeholder import draw_placeholder_svg

logger = logging.getLogger(__name__)

#: A single illustration is a slow call. Generous, but bounded — a hung provider
#: must not hold a Celery worker forever.
REQUEST_TIMEOUT_SECONDS = 120.0


@runtime_checkable
class ImageProvider(Protocol):
    """One image model, reduced to what the illustration service needs."""

    name: str

    def is_configured(self) -> bool: ...

    def generate(self, prompt: str, *, seed: int | None = None) -> bytes:
        """Render `prompt` and return the raw image bytes.

        `seed` is the consistency handle from §5.8: the service derives it from
        the character hash, so the same character can ask the model for the same
        starting point every time. Providers whose API has no seed parameter
        ignore it — consistency then rests entirely on the canonical descriptor
        and the reference set, which is where most of it lives anyway (§8.2).
        """
        ...


class CloudflareImageProvider:
    """FLUX on Cloudflare Workers AI — the implemented real provider.

    Called over plain HTTP rather than through an SDK: the request is one POST
    with two fields. The response is the part worth writing code for — Workers
    AI answers some image models with a JSON envelope carrying base64, and
    others with the raw image — so both are handled instead of assuming one.
    """

    name = "cloudflare"
    credential_env_var = "CLOUDFLARE_API_TOKEN"
    base_url = "https://api.cloudflare.com/client/v4"

    def __init__(self, account_id: str = "", api_token: str = "", model: str = "") -> None:
        self._account_id = account_id or settings.CLOUDFLARE_ACCOUNT_ID
        self._api_token = api_token or settings.CLOUDFLARE_API_TOKEN
        self._model = model or settings.IMAGE_MODEL

    def is_configured(self) -> bool:
        # Both halves, not just the token: an account id without a token (or the
        # reverse) is a misconfiguration that would otherwise surface as a 404
        # from Cloudflare in the middle of a child's story.
        return bool(self._account_id and self._api_token)

    def generate(self, prompt: str, *, seed: int | None = None) -> bytes:
        import httpx

        # Only `prompt` and `steps`: flux-1-schnell's published input schema is
        # exactly those two, and Workers AI rejects the whole request rather
        # than ignoring an unknown field, so `seed` is deliberately not sent.
        payload = {"prompt": prompt, "steps": settings.IMAGE_STEPS}
        url = f"{self.base_url}/accounts/{self._account_id}/ai/run/{self._model}"

        try:
            response = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_token}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise MediaProviderError(f"Could not reach the image provider: {exc}") from exc

        if response.status_code >= 400:
            raise MediaProviderError(
                f"Image provider returned {response.status_code} for model "
                f"'{self._model}': {response.text[:300]}"
            )

        if response.headers.get("content-type", "").startswith("application/json"):
            return self._from_envelope(response.json())
        return response.content

    def _from_envelope(self, body: dict[str, Any]) -> bytes:
        if not body.get("success", True):
            errors = body.get("errors") or body.get("messages") or "no detail"
            raise MediaProviderError(f"Image provider declined the request: {str(errors)[:300]}")

        result = body.get("result") or {}
        encoded = result.get("image")
        if not encoded:
            raise MediaProviderError(f"Image provider returned no image: {str(body)[:300]}")

        try:
            return base64.b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise MediaProviderError(f"Image payload was not valid base64: {exc}") from exc


class LocalPlaceholderImageProvider:
    """Deterministic offline art. Never a substitute for a real render.

    Draws an SVG from the prompt's CHARACTERS section (which decides the
    creature) and its SCENE section (which decides the setting), so the same
    companion appears recognisably the same across different scenes — the exact
    property §8.2 asks a real model to hold. It is labelled `local-placeholder`
    on every row it produces so nobody mistakes it for the shipping look.
    """

    name = "local"
    credential_env_var = ""
    provider_label = "local-placeholder"

    def is_configured(self) -> bool:
        # Always available on purpose: it is the reason a developer with no keys
        # can still run the whole pipeline. Choosing it is explicit —
        # IMAGE_PROVIDER=local — never a silent fallback.
        return True

    def generate(self, prompt: str, *, seed: int | None = None) -> bytes:
        return draw_placeholder_svg(prompt)


class _UnimplementedImageProvider:
    """Typed seam for an image vendor that is declared but not built."""

    name = "unimplemented"
    credential_env_var = ""

    def is_configured(self) -> bool:
        return False

    def generate(self, prompt: str, *, seed: int | None = None) -> bytes:
        raise NotImplementedError(f"Image provider '{self.name}' is not implemented yet.")


class TogetherImageProvider(_UnimplementedImageProvider):
    """Fallback seam — FLUX via Together. Not implemented."""

    name = "together"


class OpenAIImageProvider(_UnimplementedImageProvider):
    """Fallback seam — OpenAI images. Not implemented."""

    name = "openai"


class GoogleImageProvider(_UnimplementedImageProvider):
    """Fallback seam — Google Imagen. Not implemented."""

    name = "google"


IMAGE_PROVIDERS: dict[str, type] = {
    CloudflareImageProvider.name: CloudflareImageProvider,
    LocalPlaceholderImageProvider.name: LocalPlaceholderImageProvider,
    TogetherImageProvider.name: TogetherImageProvider,
    OpenAIImageProvider.name: OpenAIImageProvider,
    GoogleImageProvider.name: GoogleImageProvider,
}


def provider_label(provider: ImageProvider) -> str:
    """What to record in `Media.provider` — the honest name, not the slot."""
    return getattr(provider, "provider_label", provider.name)


def select_image_provider() -> ImageProvider:
    """The provider named by `IMAGE_PROVIDER`, if it can actually be called."""
    name = settings.IMAGE_PROVIDER
    factory = IMAGE_PROVIDERS.get(name)
    if factory is None:
        raise MediaConfigurationError(
            f"Unknown image provider '{name}' in IMAGE_PROVIDER. "
            f"Known providers: {', '.join(sorted(IMAGE_PROVIDERS))}."
        )

    provider = factory()
    if not provider.is_configured():
        # Deliberately not a fallback to `local`: shipping placeholder art
        # because a key was missing is the kind of failure nobody notices until
        # a child is looking at it.
        raise MediaConfigurationError(
            f"Image provider '{name}' has no credentials. Set CLOUDFLARE_ACCOUNT_ID "
            "and CLOUDFLARE_API_TOKEN in your .env, or set IMAGE_PROVIDER=local to "
            "draw offline placeholder art while developing."
        )
    return provider
