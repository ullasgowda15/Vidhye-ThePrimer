"""The quota breaker: what it remembers, and what it refuses to assume.

A vendor whose allowance is spent answers *no* in milliseconds, and that round
trip is on the child's critical path — paid again on the very next call, because
nothing remembered the last one. ElevenLabs answering `quota_exceeded` for every
line of a sitting was 21 seconds of dead time in the Day-6 review, on live keys.

The three things worth testing here are the three a naive "burned today" flag gets
wrong: that reset windows differ per vendor, that an exhausted allowance and a
burst rate-limit are both HTTP 429 and must not share a cooldown, and that an
unreachable cache means "try the provider" rather than "skip it".
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.media import quota
from apps.media.exceptions import MediaProviderError


class _FakeRedis:
    """Enough of redis-py for this module, with expiry recorded rather than timed."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    def setex(self, key: str, seconds: int, value: str) -> None:
        self.store[key] = seconds

    def exists(self, key: str) -> int:
        return int(key in self.store)

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def ttl(self, key: str) -> int:
        return self.store.get(key, -2)


@pytest.fixture
def cache(monkeypatch: Any) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(quota, "_client", lambda: fake)
    return fake


class TestTellingAnExhaustedAllowanceFromABurstLimit:
    """Both are 429. Conflating them would take a **healthy** provider offline for
    six hours the first time it was asked too quickly — strictly worse than the
    wasted round trip this module exists to remove."""

    @pytest.mark.parametrize(
        "message",
        [
            # Cloudflare Workers AI, copied off the wire rather than from a
            # build note. The paraphrase ("daily allocation") is what the pattern
            # was first written against, and it does not appear in the real
            # message — so an exhausted account got the burst-limit cooldown.
            '{"errors":[{"message":"AiError: AiError: you have used up your daily free '
            "allocation of 10,000 neurons, please upgrade to Cloudflare's Workers Paid "
            'plan if you would like to continue usage.","code":3006}]}',
            '{"errors":[{"message":"daily allocation exhausted","code":3006}]}',
            # ElevenLabs.
            '{"detail":{"status":"quota_exceeded"}}',
            "Quota exceeded for this billing period",
        ],
        ids=["cloudflare-real", "cloudflare-short", "elevenlabs-quota", "quota-exceeded"],
    )
    def test_a_spent_allowance_is_recognised(self, message: str) -> None:
        assert quota.looks_exhausted(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "429 Too Many Requests",
            "Rate limit reached, please slow down",
            # Vertex's generic 429. It reads like the strongest possible signal
            # and means only "not right now" — the per-minute limit on an image
            # model returns it. Treating it as a spent allowance benched Gemini
            # for 24 hours the first time three choice icons went out together.
            "429 RESOURCE_EXHAUSTED. {'error': {'message': 'Resource has been exhausted "
            "(e.g. check quota).', 'status': 'RESOURCE_EXHAUSTED'}}",
            "Image provider returned 500: upstream error",
            "Could not reach the image provider: timed out",
        ],
        ids=["bare-429", "rate-limit", "vertex-429", "server-error", "timeout"],
    )
    def test_an_ordinary_failure_is_not(self, message: str) -> None:
        assert quota.looks_exhausted(message) is False

    def test_the_two_get_very_different_cooldowns(self) -> None:
        spent = quota.cooldown_for("cloudflare", "daily allocation exhausted")
        throttled = quota.cooldown_for("cloudflare", "429 Too Many Requests")

        assert spent == quota.COOLDOWN_S["cloudflare"]
        assert throttled == quota.UNKNOWN_LIMIT_COOLDOWN_S
        assert throttled < spent, "a burst limit must not rest a provider like a spent day"


class TestResetWindowsAreNotTheSameEverywhere:
    """Cloudflare meters a **day**, ElevenLabs a **month**. One global "resets at
    midnight" would retry ElevenLabs every morning for three weeks after it
    actually ran out — the same wasted call, on a longer loop."""

    def test_each_vendor_rests_for_its_own_window(self, cache: _FakeRedis) -> None:
        quota.mark_exhausted("cloudflare", "daily allocation exhausted")
        quota.mark_exhausted("elevenlabs", "quota_exceeded")

        assert cache.store[quota._key("cloudflare")] == 6 * 3600
        assert cache.store[quota._key("elevenlabs")] == 24 * 3600

    def test_an_unknown_provider_gets_the_short_default(self, cache: _FakeRedis) -> None:
        """Unknown means nobody has checked what it meters, so the cooldown is an
        hour rather than a day: waking early costs one call."""
        quota.mark_exhausted("someone-new", "quota exceeded")

        assert cache.store[quota._key("someone-new")] == quota.DEFAULT_COOLDOWN_S


class TestWhatTheRestOfTheSystemAsksIt:
    def test_a_marked_provider_reads_as_exhausted(self, cache: _FakeRedis) -> None:
        assert quota.is_exhausted("cloudflare") is False

        quota.mark_exhausted("cloudflare", MediaProviderError("daily allocation exhausted"))

        assert quota.is_exhausted("cloudflare") is True
        assert quota.remaining_s("cloudflare") == 6 * 3600

    def test_clearing_puts_it_back_in_service(self, cache: _FakeRedis) -> None:
        """A topped-up account, or an operator who knows better than the guess."""
        quota.mark_exhausted("elevenlabs", "quota_exceeded")

        quota.clear("elevenlabs")

        assert quota.is_exhausted("elevenlabs") is False
        assert quota.remaining_s("elevenlabs") == 0

    def test_one_provider_resting_says_nothing_about_another(self, cache: _FakeRedis) -> None:
        quota.mark_exhausted("cloudflare", "daily allocation exhausted")

        assert quota.is_exhausted("google") is False


class TestAnUnreachableCacheFailsOpen:
    """Not knowing has to mean "try it". Being wrong that way costs one wasted
    call; being wrong the other way switches a provider off for as long as Redis
    is down — which is the outage this module was written to prevent, arriving by
    a different road."""

    @pytest.fixture
    def no_cache(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(quota, "_client", lambda: None)

    def test_nothing_is_skipped_when_the_cache_is_gone(self, no_cache: None) -> None:
        assert quota.is_exhausted("cloudflare") is False
        assert quota.remaining_s("cloudflare") == 0

    def test_marking_does_not_raise_into_the_handler(self, no_cache: None) -> None:
        """`mark_exhausted` is called from an `except` block on the child's path.
        Replacing the vendor's error with a cache error there would lose the only
        message that says what actually went wrong."""
        assert quota.mark_exhausted("cloudflare", "daily allocation exhausted") == 0

    def test_a_raising_client_is_treated_as_no_cache(self, monkeypatch: Any) -> None:
        class Exploding:
            def exists(self, key: str) -> int:
                raise RuntimeError("redis went away mid-call")

            def setex(self, *args: Any) -> None:
                raise RuntimeError("redis went away mid-call")

        monkeypatch.setattr(quota, "_client", lambda: Exploding())

        assert quota.is_exhausted("cloudflare") is False
        assert quota.mark_exhausted("cloudflare", "daily allocation exhausted") == 0


class TestTheChainSkipsARestingProviderWithoutLosingIt:
    """A cooldown costs a vendor its place in the queue and nothing more.

    Dropping it would be wrong twice over: `quota` *guesses* reset windows, and a
    chain that removed its last member on that guess would raise "no provider
    available" for a child whose primary was in fact fine.
    """

    def test_a_resting_primary_moves_behind_a_healthy_fallback(
        self, cache: _FakeRedis, settings: Any, monkeypatch: Any
    ) -> None:
        from apps.media.adapters import image as image_adapters

        settings.IMAGE_PROVIDER = "cloudflare"
        settings.IMAGE_FALLBACK_PROVIDERS = ["google"]
        monkeypatch.setattr(
            image_adapters.CloudflareImageProvider, "is_configured", lambda self: True
        )
        monkeypatch.setattr(image_adapters.GoogleImageProvider, "is_configured", lambda self: True)

        assert [p.name for p in image_adapters.select_image_providers()] == ["cloudflare", "google"]

        quota.mark_exhausted("cloudflare", "daily allocation exhausted")

        # Still both, and cloudflare is still *there* — just no longer first.
        assert [p.name for p in image_adapters.select_image_providers()] == ["google", "cloudflare"]

    def test_every_provider_resting_still_yields_a_full_chain(
        self, cache: _FakeRedis, settings: Any, monkeypatch: Any
    ) -> None:
        """The case that would be an outage if a cooldown removed providers."""
        from apps.media.adapters import image as image_adapters

        settings.IMAGE_PROVIDER = "cloudflare"
        settings.IMAGE_FALLBACK_PROVIDERS = ["google"]
        monkeypatch.setattr(
            image_adapters.CloudflareImageProvider, "is_configured", lambda self: True
        )
        monkeypatch.setattr(image_adapters.GoogleImageProvider, "is_configured", lambda self: True)

        quota.mark_exhausted("cloudflare", "daily allocation exhausted")
        quota.mark_exhausted("google", "RESOURCE_EXHAUSTED")

        assert len(image_adapters.select_image_providers()) == 2

    def test_a_failing_provider_is_marked_and_the_next_one_draws(
        self, cache: _FakeRedis, settings: Any, monkeypatch: Any
    ) -> None:
        """The whole feature, end to end: Cloudflare says the day is spent, Imagen
        draws the picture, and the row records who really drew it."""
        from apps.media.adapters import image as image_adapters

        settings.IMAGE_PROVIDER = "cloudflare"
        settings.IMAGE_FALLBACK_PROVIDERS = ["google"]
        monkeypatch.setattr(
            image_adapters.CloudflareImageProvider, "is_configured", lambda self: True
        )
        monkeypatch.setattr(image_adapters.GoogleImageProvider, "is_configured", lambda self: True)
        monkeypatch.setattr(
            image_adapters.CloudflareImageProvider,
            "generate",
            lambda self, prompt, **kw: (_ for _ in ()).throw(
                MediaProviderError('{"errors":[{"message":"daily allocation exhausted"}]}')
            ),
        )
        monkeypatch.setattr(
            image_adapters.GoogleImageProvider, "generate", lambda self, prompt, **kw: b"JPEGBYTES"
        )

        data, provider = image_adapters.generate_with_failover(
            "a mossy riverbank at dawn", negative_prompt_for=lambda: "no text", seed=7
        )

        assert data == b"JPEGBYTES"
        assert provider.name == "google"
        # And the failure is remembered, so the next beat does not pay for it.
        assert quota.is_exhausted("cloudflare") is True

    def test_a_chain_that_fails_end_to_end_raises_the_last_error(
        self, cache: _FakeRedis, settings: Any, monkeypatch: Any
    ) -> None:
        """Failover changes what a child usually sees, never what happens when
        everything is out: `media.service` still gets an error, still substitutes
        stock art, still files an incident."""
        from apps.media.adapters import image as image_adapters

        settings.IMAGE_PROVIDER = "cloudflare"
        settings.IMAGE_FALLBACK_PROVIDERS = ["google"]
        monkeypatch.setattr(
            image_adapters.CloudflareImageProvider, "is_configured", lambda self: True
        )
        monkeypatch.setattr(image_adapters.GoogleImageProvider, "is_configured", lambda self: True)
        for cls, message in (
            (image_adapters.CloudflareImageProvider, "cloudflare is out"),
            (image_adapters.GoogleImageProvider, "imagen refused the prompt"),
        ):
            monkeypatch.setattr(
                cls,
                "generate",
                lambda self, prompt, _m=message, **kw: (_ for _ in ()).throw(
                    MediaProviderError(_m)
                ),
            )

        with pytest.raises(MediaProviderError, match="imagen refused the prompt"):
            image_adapters.generate_with_failover(
                "a mossy riverbank", negative_prompt_for=lambda: "", seed=None
            )


class TestOurOwnMisconfigurationIsNotAVendorsQuota:
    """A missing key is not a spent allowance, and resting a provider for it is
    the wrong lesson twice: the cooldown fixes nothing, and a deployment that
    briefly lost a key would go on skipping a healthy vendor for a day after the
    key came back.

    Caught before it shipped. The suite blanks every credential, so the first run
    after this module existed wrote a 24-hour ElevenLabs cooldown into a
    developer's own Redis on the strength of "no API key" — visible only because
    `manage.py quota` was run by hand a minute later.
    """

    def test_a_configuration_error_is_never_recorded(self, cache: _FakeRedis) -> None:
        from apps.media.exceptions import MediaConfigurationError

        recorded = quota.mark_exhausted(
            "elevenlabs", MediaConfigurationError("ELEVENLABS_API_KEY is not set.")
        )

        assert recorded == 0
        assert quota.is_exhausted("elevenlabs") is False

    def test_a_provider_error_still_is(self, cache: _FakeRedis) -> None:
        """The other half, so the guard above cannot be widened into 'record
        nothing' without a test noticing."""
        assert quota.mark_exhausted("elevenlabs", MediaProviderError("quota_exceeded")) > 0
        assert quota.is_exhausted("elevenlabs") is True
