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
* ``google`` — Imagen on Vertex AI, the failover for a spent Cloudflare
  allocation (`IMAGE_FALLBACK_PROVIDERS`). It runs on the service account this
  deployment already has, and it costs §8.2 consistency: it has never seen the
  companion's FLUX reference set, so a fallback beat may not match. That trade
  was made deliberately, and `Media.provider` records which model drew each row.
* ``together`` / ``openai`` — declared, typed, unimplemented (§19.3's fallback
  seam). They raise rather than silently not existing.

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
import time
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


class GoogleImageProvider:
    """Imagen on Vertex AI — the fallback for when Cloudflare's day is spent.

    Built because the free Workers AI allocation is 10,000 neurons a day and a
    single sitting spends a visible fraction of it: when it runs out mid-story the
    child was getting safe stock art for the rest of the adventure. The founder's
    call, made with the trade below stated, is that a real picture is worth more
    than a matching one.

    **What this costs, said plainly.** §8.2's character consistency lives in the
    Cloudflare reference set, and Imagen has never seen it. A beat illustrated
    here may show a companion that does not look like the companion — the same
    creature described the same way by a different model, which is not the same
    picture. `media.service` records the real provider on the row
    (`provider_label`), so a parent reading the transparency page and a reviewer
    reading `media.generated` can both tell which beats came from where.

    Credentials, project and location are the ones `apps.common.vertex` already
    holds: one service account for every Google route that touches child data
    (§13.8). There is no second key to set.
    """

    name = "google"
    #: This is a *language* model drawing a picture, so the negatives go in as an
    #: instruction it can read rather than as a `negative_prompt` field — which is
    #: why the flag is `True` here while `CloudflareImageProvider`, a diffusion
    #: model with no such field, leaves it `False`. Same §15.9 rule, opposite
    #: answer, for the reason the capability flags exist.
    supports_negative_prompt = True
    #: No seed on this API. Declared honestly rather than accepting one and
    #: dropping it: §5.8's consistency handle simply is not available here, and a
    #: provider that pretended otherwise would make the fallback's already-weaker
    #: character continuity look like a bug in the seed derivation.
    supports_seed = False
    supports_image_reference = False

    def __init__(self, model: str = "", location: str = "") -> None:
        self._model = model or settings.GOOGLE_IMAGE_MODEL
        self._location = location or settings.GOOGLE_IMAGE_LOCATION

    @property
    def provider_label(self) -> str:
        """The honest name for `Media.provider`, so a fallback beat is findable."""
        return f"google:{self._model}"

    def is_configured(self) -> bool:
        from apps.common import vertex

        return vertex.is_configured()

    def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        seed: int | None = None,
    ) -> bytes:
        from google.genai import types

        from apps.common import vertex

        # `generate_content` with an IMAGE modality, not `generate_images`.
        # Imagen is the obvious API and it is not available: every
        # `imagen-*` id 404s on this project in `global` *and* in `us-central1`,
        # because Imagen is separately allowlisted. `gemini-2.5-flash-image` is
        # served by the same endpoint, the same service account and the same
        # region as the beat model, so the fallback needs no second entitlement
        # to acquire and no second thing to go wrong at 3am.
        contents = prompt
        if negative_prompt:
            # No `negative_prompt` field on this API. Appended as its own labelled
            # clause rather than folded into the scene description, because
            # §15.9's negatives are a safety control and a diffusion model reading
            # "no text" as the tokens *text* is the bug that painted a watermark
            # into a picture. A language model reads the instruction as one.
            contents = f"{prompt}\n\nDo not include any of the following: {negative_prompt}"

        try:
            response = vertex.client(location=self._location).models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
        except Exception as exc:
            raise MediaProviderError(
                f"Gemini image request failed for '{self._model}': {exc}"
            ) from exc

        for candidate in getattr(response, "candidates", None) or []:
            for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
                blob = getattr(part, "inline_data", None)
                if blob is not None and getattr(blob, "data", None):
                    return bytes(blob.data)

        # A refused prompt comes back as text with no image part rather than as an
        # error, so this has to read as a refusal: a caller that treated it as a
        # transport blip would retry something the vendor has already declined.
        raise MediaProviderError(
            f"Gemini returned no image for '{self._model}' — the prompt was most "
            f"likely refused, or answered with words instead of a picture."
        )


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


def select_image_providers() -> list[ImageProvider]:
    """The primary followed by every configured fallback, quota-aware.

    Mirrors `tts.select_tts_providers` in shape, and differs from it in what a
    caller may assume: an empty `IMAGE_FALLBACK_PROVIDERS` gives a one-element
    chain, which is the behaviour this module had before failover existed.

    A provider inside a `media.quota` cooldown is moved to the **back** rather
    than removed. `quota` guesses reset windows, and a chain that dropped its last
    member on that guess would raise "nothing configured" for a child whose
    primary was in fact fine — so a cooldown costs a vendor its place in the queue
    and nothing more.

    A fallback that is unknown or uncredentialled is skipped with a log line
    rather than raising. The primary is the one whose misconfiguration is an
    error, because that is the one somebody chose on purpose.
    """
    from apps.media import quota

    chain: list[ImageProvider] = [select_image_provider()]
    for name in settings.IMAGE_FALLBACK_PROVIDERS:
        if name == settings.IMAGE_PROVIDER:
            continue
        factory = IMAGE_PROVIDERS.get(name)
        if factory is None:
            logger.warning("media.image_fallback_unknown provider=%s", name)
            continue
        candidate = factory()
        if not candidate.is_configured():
            logger.info("media.image_fallback_unconfigured provider=%s", name)
            continue
        chain.append(candidate)

    live = [p for p in chain if not quota.is_exhausted(p.name)]
    resting = [p for p in chain if quota.is_exhausted(p.name)]
    if resting:
        logger.info(
            "media.image_quota_skip resting=%s trying=%s",
            ",".join(p.name for p in resting),
            ",".join(p.name for p in live) or "-",
        )
    return live + resting


def generate_with_failover(
    prompt: str,
    *,
    negative_prompt_for: Any,
    seed: int | None,
) -> tuple[bytes, ImageProvider]:
    """Draw `prompt`, trying each configured provider in turn.

    Returns the bytes and the provider that produced them, because the caller
    records the real one on the row — a fallback beat has to be findable
    afterwards, both for a parent reading the transparency page and for anyone
    asking why one picture in an adventure looks unlike the others (§8.2).

    `negative_prompt_for` is a callable rather than a string: the §15.9 negatives
    are only sent to providers that declare a real `negative_prompt` field, and
    which provider is being asked is not known until the loop is running.

    If the whole chain fails the **last** error is raised, so `media.service`
    treats it exactly as it treated a single provider's failure — stock art, a
    playable beat, and an incident. Failover changes what a child usually sees,
    never what happens when everything is out.
    """
    from apps.media import quota

    chain = select_image_providers()
    last_error: Exception | None = None

    for index, provider in enumerate(chain):
        started = time.monotonic()
        try:
            return (
                provider.generate(
                    prompt,
                    negative_prompt=(
                        negative_prompt_for() if supports(provider, "supports_negative_prompt") else ""
                    ),
                    seed=seed if supports(provider, "supports_seed") else None,
                ),
                provider,
            )
        except (MediaProviderError, MediaConfigurationError) as exc:
            last_error = exc
            quota.mark_exhausted(provider.name, exc)
            remaining = chain[index + 1 :]
            logger.warning(
                "media.image_failover failed=%s after_ms=%d next=%s error=%s",
                provider.name,
                (time.monotonic() - started) * 1000,
                remaining[0].name if remaining else "-",
                exc,
            )

    raise last_error or MediaConfigurationError("No image provider was available.")
