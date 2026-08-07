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

**Capabilities are declared, not assumed.** Each provider states whether it can
take a reference image, a negative prompt and a seed, and the pipeline sends
only what the chosen provider can consume. The alternative — pack everything
into the prompt string and hope — is not a graceful degradation: it is how a
file path and the word "text" ended up being drawn into a picture. Every flag
here defaults to `False`, so a new adapter is honest until it proves otherwise.
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

    #: Can this model be conditioned on an anchor image? When `False` the
    #: `REFERENCES:` block is never built, because a text-only model would read
    #: the URL as words to draw rather than a picture to match.
    supports_image_reference: bool

    #: Does this model's API have a real `negative_prompt` field? When `False`
    #: the §15.9 negatives are dropped, never folded into the positive prompt.
    supports_negative_prompt: bool

    #: Does this model's API accept a seed? When `False` the service does not
    #: send one, rather than passing a value that is silently discarded.
    supports_seed: bool

    def is_configured(self) -> bool: ...

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> bytes:
        """Render `prompt` and return the raw image bytes.

        The service sends `negative_prompt` and `seed` only to providers that
        declare support for them, so an implementation never has to decide what
        to do with an argument it cannot use.

        `seed` is the consistency handle from §5.8: the service derives it from
        the character hash, so the same character can ask the model for the same
        starting point every time. Where it is unavailable, consistency rests
        entirely on the canonical descriptor (§8.2) — which today is everywhere.
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

    # flux-1-schnell's published input schema is `prompt` and `steps`, and
    # Workers AI rejects the whole request rather than ignoring an unknown
    # field. So: no reference image (it is text-only and cannot fetch one), no
    # negative prompt, no seed. Consistency here is the descriptor, honestly.
    # Wiring an image-conditioning provider is a later upgrade, not a flag flip.
    supports_image_reference = False
    supports_negative_prompt = False
    supports_seed = False

    def __init__(self, account_id: str = "", api_token: str = "", model: str = "") -> None:
        self._account_id = account_id or settings.CLOUDFLARE_ACCOUNT_ID
        self._api_token = api_token or settings.CLOUDFLARE_API_TOKEN
        self._model = model or settings.IMAGE_MODEL

    def is_configured(self) -> bool:
        # Both halves, not just the token: an account id without a token (or the
        # reverse) is a misconfiguration that would otherwise surface as a 404
        # from Cloudflare in the middle of a child's story.
        return bool(self._account_id and self._api_token)

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> bytes:
        import httpx

        # Only `prompt` and `steps` — see the capability flags above. The
        # service does not send the other two; the signature accepts them so the
        # Protocol holds, and they are ignored rather than smuggled into
        # `prompt`, which is the bug this shape exists to make impossible.
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

    # It draws from the prompt's own sections and nothing else. Claiming any
    # capability here would let the offline path exercise a code branch the real
    # provider never takes, which is the opposite of what this adapter is for.
    supports_image_reference = False
    supports_negative_prompt = False
    supports_seed = False

    def is_configured(self) -> bool:
        # Always available on purpose: it is the reason a developer with no keys
        # can still run the whole pipeline. Choosing it is explicit —
        # IMAGE_PROVIDER=local — never a silent fallback.
        return True

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> bytes:
        return draw_placeholder_svg(prompt)


class _UnimplementedImageProvider:
    """Typed seam for an image vendor that is declared but not built."""

    name = "unimplemented"
    credential_env_var = ""
    supports_image_reference = False
    supports_negative_prompt = False
    supports_seed = False

    def is_configured(self) -> bool:
        return False

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> bytes:
        raise NotImplementedError(f"Image provider '{self.name}' is not implemented yet.")


class TogetherImageProvider(_UnimplementedImageProvider):
    """Fallback seam — FLUX via Together. Not implemented.

    Declared capabilities describe the *vendor API*, not this stub: Together's
    FLUX endpoint takes `negative_prompt` and `seed`. They are recorded now so
    the day someone writes `generate()` the pipeline already sends the right
    fields — and so the capability gating has a provider to be tested against
    that is not the one we ship.
    """

    name = "together"
    supports_negative_prompt = True
    supports_seed = True


class OpenAIImageProvider(_UnimplementedImageProvider):
    """Fallback seam — OpenAI images. Not implemented.

    The image-edit endpoint is conditioned on a supplied picture, so this is the
    seam a real §8.2 consistency mechanism would land on. It has no negative
    prompt.
    """

    name = "openai"
    supports_image_reference = True


class GoogleImageProvider(_UnimplementedImageProvider):
    """Fallback seam — Google Imagen. Not implemented. Takes negatives + seed."""

    name = "google"
    supports_negative_prompt = True
    supports_seed = True


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


def supports(provider: ImageProvider, capability: str) -> bool:
    """Whether `provider` declares `capability`. Silence means no.

    Defaulting to `False` is the point: an adapter that has not thought about
    negative prompts must not be sent one, and a test double must not
    accidentally exercise a branch the shipping provider never takes.
    """
    return bool(getattr(provider, capability, False))


def configured_supports_image_reference() -> bool:
    """Whether the configured provider can be conditioned on an anchor image.

    Answered from the class named by `IMAGE_PROVIDER`, without constructing it
    and without touching credentials: prompt assembly happens *before* the cache
    lookup, and a cache hit must never depend on a key being present. An unknown
    provider name answers `False` rather than raising — `select_image_provider`
    is where that misconfiguration is reported, and only on a real cache miss.
    """
    factory = IMAGE_PROVIDERS.get(settings.IMAGE_PROVIDER)
    return bool(getattr(factory, "supports_image_reference", False))


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
