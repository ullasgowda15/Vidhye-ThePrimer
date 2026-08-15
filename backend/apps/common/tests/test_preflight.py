"""Credential probes (PRODUCT.md §16.10, §16.6).

The module under test exists because of one afternoon: three providers whose
credentials were the *words for* the credentials, a preflight that said READY, and
a child who waited twenty seconds for a story with no picture and nothing spoken.

Two properties matter, and they pull in opposite directions:

* it must catch an unfilled blank — that is the whole job;
* it must not catch anything else, because a preflight with a false positive
  gets skipped, and a skipped preflight is the state we were already in.

So the placeholder tests come in pairs, and the probe tests assert on what a
vendor's refusal *becomes* rather than on it being noticed at all: a `Probe` that
carries the vendor's own words is the difference between a person fixing the
project and a person re-reading their `.env`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.common import preflight


class TestSpottingAnUnfilledBlank:
    @pytest.mark.parametrize(
        "value",
        (
            "YOUR_CLOUDFLARE_ACCOUNT_ID",
            "YOUR_ELEVENLABS_VOICE_ID",
            "your-api-key",
            "change-me-to-at-least-32-random-bytes-before-you-deploy",
            "django-insecure-day1-change-me",
            "changeme",
            "replace_me",
            "<paste the project id>",
            "paste-it-here",
            "TODO",
            "xxxx",
            "placeholder",
        ),
    )
    def test_it_catches_the_shapes_a_blank_arrives_in(self, value: str) -> None:
        assert preflight.looks_like_a_placeholder(value)

    @pytest.mark.parametrize(
        "value",
        (
            "",
            "9a7c1f2e4b6d8a0c2e4f6a8b0c2d4e6f",  # a Cloudflare account id
            "21m00Tcm4TlvDq8ikWAM",  # an ElevenLabs voice id
            "sk-ant-api03-Zx9",
            "ullasgowda3523-app",  # a real GCP project id, and the one from the incident
            "/home/ops/.config/primer/vertex-sa.json",
            "6H0y0AGRTP-cKQ0RRW9x8p2yh4Y3iCJqQyLwLQ4qSAI=",
            "primer-media-eu",
        ),
    )
    def test_it_leaves_a_real_credential_alone(self, value: str) -> None:
        """Including `ullasgowda3523-app`, which was *wrong* and was not a
        placeholder: a project that answers 403 is spelled perfectly, and nothing
        offline can tell. That is `--live`'s job, and pretending otherwise here
        would be the false positive that gets this check switched off."""
        assert not preflight.looks_like_a_placeholder(value)

    def test_it_reports_the_setting_and_the_value(self, settings: Any) -> None:
        """Both halves. A row saying `CLOUDFLARE_ACCOUNT_ID is wrong` sends
        somebody looking; one saying `=YOUR_CLOUDFLARE_ACCOUNT_ID` ends it."""
        settings.CLOUDFLARE_ACCOUNT_ID = "YOUR_CLOUDFLARE_ACCOUNT_ID"
        settings.ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

        found = preflight.placeholders()
        assert "CLOUDFLARE_ACCOUNT_ID=YOUR_CLOUDFLARE_ACCOUNT_ID" in found
        assert not [row for row in found if row.startswith("ELEVENLABS_VOICE_ID")]


def _answering(status: int, body: str = "") -> Any:
    """An httpx stub that gives every request the same answer."""

    def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(status_code=status, text=body, request=httpx.Request(method, url))

    return request


class TestTheVendorProbes:
    def test_an_unconfigured_vendor_is_not_probed(self, settings: Any) -> None:
        """Absent is not broken. A deployment with no Cloudflare account has not
        failed a Cloudflare check — `launch_check` decides whether that absence
        matters, from whether the provider is a primary or a fallback."""
        settings.CLOUDFLARE_ACCOUNT_ID = ""
        settings.CLOUDFLARE_API_TOKEN = ""

        probe = preflight.probe_cloudflare()
        assert probe.ok
        assert "no CLOUDFLARE_ACCOUNT_ID" in probe.detail

    def test_a_refused_account_carries_the_vendors_own_words(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        """Workers AI answers a wrong account id with a 404 naming the *model*,
        which reads like a model that has been withdrawn. Carrying the body
        through is what stops an afternoon being spent on the wrong question."""
        settings.CLOUDFLARE_ACCOUNT_ID = "YOUR_CLOUDFLARE_ACCOUNT_ID"
        settings.CLOUDFLARE_API_TOKEN = "cf-token"
        monkeypatch.setattr(httpx, "request", _answering(404, '{"code":7003}'))

        probe = preflight.probe_cloudflare()
        assert not probe.ok
        assert "404" in probe.detail
        assert "7003" in probe.detail

    def test_a_working_account_passes(self, settings: Any, monkeypatch: Any) -> None:
        settings.CLOUDFLARE_ACCOUNT_ID = "9a7c1f2e4b6d8a0c2e4f6a8b0c2d4e6f"
        settings.CLOUDFLARE_API_TOKEN = "cf-token"
        monkeypatch.setattr(httpx, "request", _answering(200, "{}"))

        assert preflight.probe_cloudflare().ok

    def test_a_refused_voice_names_itself(self, settings: Any, monkeypatch: Any) -> None:
        """The incident had the key and the voice wrong at once, and they fail
        identically from the outside — the voice id is in the URL, so both are
        just a silent narration. Naming the voice in the row separates them."""
        settings.ELEVENLABS_API_KEY = "el-key"
        settings.ELEVENLABS_VOICE_ID = "YOUR_ELEVENLABS_VOICE_ID"
        settings.ELEVENLABS_VOICE_MAP = {}
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kwargs: httpx.Response(
                status_code=401,
                text='{"code":"unauthorized"}',
                request=httpx.Request("POST", url),
            ),
        )

        probe = preflight.probe_elevenlabs()
        assert not probe.ok
        assert "YOUR_ELEVENLABS_VOICE_ID" in probe.detail
        assert "401" in probe.detail

    def test_every_mapped_voice_is_spoken_not_just_the_default(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        """`ELEVENLABS_VOICE_MAP` gives each archetype its own voice, so a typo in
        one is a companion that is silent while every other companion speaks."""
        settings.ELEVENLABS_API_KEY = "el-key"
        settings.ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
        settings.ELEVENLABS_VOICE_MAP = {"calm_deep": "not-a-voice"}

        def post(url: str, **kwargs: Any) -> httpx.Response:
            refused = url.endswith("not-a-voice")
            return httpx.Response(
                status_code=404 if refused else 200,
                content=b"" if refused else b"audio",
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", post)

        probe = preflight.probe_elevenlabs()
        assert not probe.ok
        assert "not-a-voice" in probe.detail
        assert "calm_deep" in probe.detail

    def test_a_key_scoped_to_speech_alone_is_not_reported_as_broken(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        """Why this probe synthesises instead of reading. A key scoped to
        text-to-speech and nothing else — the correct scope — 401s on `/v1/user`,
        `/v1/voices`, `/v1/models` and `/v1/user/subscription` with
        `missing_permissions`. Measured against a real key, not assumed. A probe
        built on any of them calls a working deployment broken, which is the false
        positive that ends with the preflight being skipped.
        """
        settings.ELEVENLABS_API_KEY = "el-key"
        settings.ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
        settings.ELEVENLABS_VOICE_MAP = {}

        def refuse_reads(method: str, url: str, **kwargs: Any) -> httpx.Response:
            raise AssertionError(f"probed {url}, which a speech-scoped key cannot read")

        monkeypatch.setattr(httpx, "request", refuse_reads)
        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, **kwargs: httpx.Response(
                status_code=200, content=b"audio", request=httpx.Request("POST", url)
            ),
        )

        assert preflight.probe_elevenlabs().ok

    def test_the_google_key_never_reaches_the_url(self, settings: Any, monkeypatch: Any) -> None:
        """A regression test with a receipt.

        httpx logs every request URL at INFO. When this probe passed the key as
        `?key=…`, running `launch_check --live` wrote it into the worker log and
        into the command's own output — and on 15 August 2026 it went into a chat
        transcript from there and had to be rotated
        (`Docs/Runbooks/Key-Rotation.md`).

        Google accepts the key either way, its quickstarts show the query-string
        form, and `adapters/tts.py` had already chosen the header for this exact
        reason. So the wrong version is the easy one to write twice, which is
        what makes it worth a test rather than a comment.
        """
        settings.GOOGLE_TTS_API_KEY = "super-secret-tts-key"
        seen: dict[str, Any] = {}

        def capture(method: str, url: str, **kwargs: Any) -> httpx.Response:
            seen["url"] = url
            seen["headers"] = kwargs.get("headers") or {}
            return httpx.Response(200, json={"voices": []})

        monkeypatch.setattr(httpx, "request", capture)

        assert preflight.probe_google_tts().ok
        assert "super-secret-tts-key" not in seen["url"]
        assert "key=" not in seen["url"]
        assert seen["headers"].get("X-Goog-Api-Key") == "super-secret-tts-key"

    def test_a_vendor_that_cannot_be_reached_is_a_failure_not_a_crash(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        """This runs in a loop over every provider. The second one's answer is as
        interesting as the first's even when the first is on fire."""
        settings.GOOGLE_TTS_API_KEY = "tts-key"

        def explode(method: str, url: str, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("name resolution failed")

        monkeypatch.setattr(httpx, "request", explode)

        probe = preflight.probe_google_tts()
        assert not probe.ok
        assert "could not be reached" in probe.detail

    def test_a_vertex_refusal_becomes_a_row_rather_than_a_traceback(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        """The 403 that started all this. `CONSUMER_INVALID` means the *project* is
        refused — billing lapsed, API never enabled, or a service-account key from
        somewhere else — so the message has to survive into the preflight's output
        intact, because it is the only part that says which."""
        from apps.common import vertex

        settings.GOOGLE_CLOUD_PROJECT = "ullasgowda3523-app"
        settings.GOOGLE_CLOUD_LOCATION = "global"
        settings.GOOGLE_APPLICATION_CREDENTIALS = "/nowhere/sa.json"

        def refuse(operation: Any, **kwargs: Any) -> Any:
            raise RuntimeError("403 PERMISSION_DENIED ... CONSUMER_INVALID")

        monkeypatch.setattr(vertex, "call", refuse)

        probe = preflight.probe_vertex("gemini-2.5-flash")
        assert not probe.ok
        assert "CONSUMER_INVALID" in probe.detail

    def test_vertex_is_skipped_rather_than_guessed_at_when_unconfigured(
        self, settings: Any
    ) -> None:
        settings.GOOGLE_CLOUD_PROJECT = ""
        settings.GOOGLE_CLOUD_LOCATION = ""
        settings.GOOGLE_APPLICATION_CREDENTIALS = ""

        probe = preflight.probe_vertex("gemini-2.5-flash")
        assert probe.ok
        assert "GOOGLE_CLOUD_PROJECT" in probe.detail


class TestItProbesWhatThisDeploymentActuallyUses:
    """The failure mode of every preflight that hardcodes its checklist: it goes
    green on a route nothing uses while the configured one is on fire."""

    def test_it_follows_the_configured_providers(self, settings: Any, monkeypatch: Any) -> None:
        settings.AI_PROVIDER_ORDER = ["google"]
        settings.AI_BEAT_MODEL = "gemini-2.5-flash"
        settings.AI_SAFETY_MODEL = "gemini-2.5-flash-lite"
        settings.IMAGE_PROVIDER = "cloudflare"
        settings.IMAGE_FALLBACK_PROVIDERS = ["google"]
        settings.TTS_PROVIDER = "elevenlabs"
        settings.TTS_FALLBACK_PROVIDERS = ["google"]
        settings.EMBEDDING_PROVIDER = "google"

        seen: list[str] = []

        def spy(label: str) -> Any:
            def probe() -> preflight.Probe:
                seen.append(label)
                return preflight.Probe(label, True, "")

            return probe

        def vertex_spy(model: str) -> preflight.Probe:
            seen.append(f"vertex:{model}")
            return preflight.Probe(model, True, "")

        for attribute, label in (
            ("probe_embeddings", "embeddings"),
            ("probe_cloudflare", "cloudflare"),
            ("probe_gemini_image", "google-image"),
            ("probe_elevenlabs", "elevenlabs"),
            ("probe_google_tts", "google-tts"),
        ):
            monkeypatch.setattr(preflight, attribute, spy(label))
        monkeypatch.setattr(preflight, "probe_vertex", vertex_spy)

        preflight.probe_all()

        assert seen == [
            "vertex:gemini-2.5-flash",
            "vertex:gemini-2.5-flash-lite",
            "embeddings",
            "cloudflare",
            "google-image",
            "elevenlabs",
            "google-tts",
        ]

    def test_an_anthropic_deployment_is_not_asked_about_vertex(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        settings.AI_PROVIDER_ORDER = ["anthropic"]
        settings.IMAGE_PROVIDER = "local"
        settings.IMAGE_FALLBACK_PROVIDERS = []
        settings.TTS_PROVIDER = "local"
        settings.TTS_FALLBACK_PROVIDERS = []
        settings.EMBEDDING_PROVIDER = "local"

        monkeypatch.setattr(
            preflight,
            "probe_vertex",
            lambda model: pytest.fail("probed Vertex on a deployment that does not use it"),
        )

        assert preflight.probe_all() == []

    def test_the_offline_embedding_provider_is_not_probed(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        """`local` is a lexical hashing trick with no key and no vendor. Probing it
        would report a deployment that cannot do semantic recall at all as a
        deployment whose embedding route is fine."""
        settings.AI_PROVIDER_ORDER = ["anthropic"]
        settings.IMAGE_PROVIDER = "local"
        settings.IMAGE_FALLBACK_PROVIDERS = []
        settings.TTS_PROVIDER = "local"
        settings.TTS_FALLBACK_PROVIDERS = []
        settings.EMBEDDING_PROVIDER = "local"

        monkeypatch.setattr(
            preflight,
            "probe_embeddings",
            lambda: pytest.fail("probed the offline embedding provider"),
        )

        assert preflight.probe_all() == []
