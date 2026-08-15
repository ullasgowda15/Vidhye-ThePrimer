"""Are this deployment's credentials real? (PRODUCT.md §16.10, §13.8.)

`launch_check` asks whether the deployment is *configured*. Every row in it reads
a setting, a database table or a file, and every one of them can be green while
not one provider will answer — which is not a hypothetical:

    POST .../accounts/YOUR_CLOUDFLARE_ACCOUNT_ID/ai/run/@cf/... 404
    POST .../v1/text-to-speech/YOUR_ELEVENLABS_VOICE_ID          401
    POST .../projects/<project>/...:generateContent              403 CONSUMER_INVALID

Three providers, all "configured", none reachable: a child got a fallback beat
with no picture and nothing spoken, and the preflight had said READY. `.env` had
been filled in with the *names* of the values rather than the values.

So this module asks the two questions that catch that, in the two ways they can
be asked:

* **`placeholders()` is free and offline.** A credential that is still the word
  describing it is not a credential. This alone would have caught two of the three
  above, on a laptop, with no network.
* **`probe_*()` costs a round trip.** The remaining failure — a project id that is
  spelled correctly and rejected anyway, because billing lapsed or the API was
  never enabled — is only visible to something that calls the vendor.

Each probe is the cheapest request that proves the credential works *at the scope
the product uses it*, which is a stricter rule than it sounds and is where the
first draft of this module was wrong twice. A preflight that asks for more
permission than the product needs fails on a correctly-scoped deployment; a
preflight that reads metadata when the product generates can pass while every
generation 404s. Both were caught against real credentials, and each probe's
docstring says which endpoint it settled on and why. Two of them therefore cost
money — a couple of ElevenLabs credits, and cents for the Gemini illustration —
and say so.

Nothing here runs on the child's path. It runs when a person is deciding whether
to let a family in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

#: Seconds any single probe may take. Short: a person is watching this run, and a
#: vendor that cannot answer a metadata GET in ten seconds has told us something
#: too.
PROBE_TIMEOUT_S = 10.0

#: The settings whose value is a credential, an account identifier or a key path —
#: everything a deployment has to *fill in* rather than accept the default of.
#: Listed here rather than derived, because "looks like a secret" is a guess and
#: this list is a decision.
CREDENTIAL_SETTINGS = (
    "SECRET_KEY",
    "MEMORY_ENCRYPTION_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "GOOGLE_TTS_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_STORAGE_BUCKET_NAME",
)

#: What an unfilled blank looks like. Deliberately narrow — a false positive here
#: refuses a launch that should have proceeded, which is the one way this check
#: could earn itself a `--skip` flag and then a habit of passing it.
#:
#: `your_`/`your-` covers the shape the incident actually arrived in
#: (`YOUR_CLOUDFLARE_ACCOUNT_ID`), the angle brackets cover `<paste it here>`, and
#: `change-me` covers this repo's own `.env.example` default for SECRET_KEY.
_PLACEHOLDER_PATTERNS = re.compile(
    r"""
    your[_-]                 # YOUR_CLOUDFLARE_ACCOUNT_ID, your-key-here
    | change[_-]?me          # change-me-to-at-least-32-random-bytes
    | replace[_-]?me
    | placeholder
    | [_-]here\b             # paste-it-here
    | ^(todo|tbd|none|null|example)$
    | ^x{4,}$                # xxxx
    | [<>]                   # <your project id>
    """,
    re.I | re.X,
)


def looks_like_a_placeholder(value: str) -> bool:
    """True when this value is the *name* of a credential rather than one."""
    text = str(value or "").strip()
    return bool(text) and bool(_PLACEHOLDER_PATTERNS.search(text))


def placeholders() -> list[str]:
    """`NAME=value` for every credential setting still holding a placeholder.

    The value is printed, which is safe precisely because of what matched: a real
    secret never reaches this list. Printing it is most of the usefulness — an
    operator reading `CLOUDFLARE_ACCOUNT_ID=YOUR_CLOUDFLARE_ACCOUNT_ID` knows what
    happened and where, and one reading `CLOUDFLARE_ACCOUNT_ID is wrong` goes
    looking.
    """
    return [
        f"{name}={value}"
        for name in CREDENTIAL_SETTINGS
        if looks_like_a_placeholder(value := str(getattr(settings, name, "") or ""))
    ]


@dataclass(frozen=True)
class Probe:
    """One vendor, asked whether the credential we hold actually works."""

    name: str
    ok: bool
    detail: str

    @classmethod
    def skipped(cls, name: str, why: str) -> Probe:
        """Not configured, so not probed — reported as a pass with its reason.

        A provider nobody has configured is absent, not broken. Which of those two
        matters is `launch_check`'s call to make (an unconfigured *fallback* is a
        warning, an unconfigured primary is a refusal); this module's job is to
        say what it found without deciding.
        """
        return cls(name=name, ok=True, detail=why)


def _http_probe(name: str, method: str, url: str, *, headers: dict[str, str]) -> Probe:
    """One request, turned into a `Probe`. Never raises.

    A probe that raised would be worse than no probe: this runs in a loop over
    every provider, and the second one's answer is as interesting as the first's
    even when the first is on fire.
    """
    import httpx

    try:
        response = httpx.request(method, url, headers=headers, timeout=PROBE_TIMEOUT_S)
    except httpx.HTTPError as exc:
        return Probe(name=name, ok=False, detail=f"could not be reached: {exc}")

    if response.status_code >= 400:
        return Probe(
            name=name,
            ok=False,
            detail=f"answered {response.status_code}: {response.text[:200]}",
        )
    return Probe(name=name, ok=True, detail="credentials accepted")


def probe_cloudflare() -> Probe:
    """Does `CLOUDFLARE_API_TOKEN` open Workers AI on `CLOUDFLARE_ACCOUNT_ID`?

    The Workers AI model list, and the endpoint choice is the whole subtlety:
    **probe the scope the product uses, not a broader one.** The obvious probe,
    `GET /accounts/{id}`, needs account-read and a correctly narrow Workers AI
    token does not have it — so the obvious probe fails on a deployment that
    works, which is the false positive that gets a preflight switched off. This
    path is under `/accounts/{id}/ai/`, so it still fails on a wrong account id,
    which is the failure that actually happened.

    Free either way. A generation would be truer still and spends the daily
    neuron allocation the illustrations need.
    """
    account = str(settings.CLOUDFLARE_ACCOUNT_ID or "")
    token = str(settings.CLOUDFLARE_API_TOKEN or "")
    if not (account and token):
        return Probe.skipped("cloudflare", "no CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN set")

    return _http_probe(
        "cloudflare",
        "GET",
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/models/search?per_page=1",
        headers={"Authorization": f"Bearer {token}"},
    )


def probe_elevenlabs() -> Probe:
    """Say one word, through the adapter a beat's narration goes through.

    **The only probe here that cannot be a read.** Every ElevenLabs metadata
    endpoint — `/v1/user`, `/v1/voices`, `/v1/models` — needs a permission
    (`user_read`, `voices_read`) that a key scoped to text-to-speech and nothing
    else does not carry, and scoping the key that narrowly is correct. Measured,
    not assumed: a working production key 401s on all four with
    `missing_permissions`. A probe that reported that as a broken key would be
    wrong about the one deployment shape we want most.

    So it synthesises. One short word, at `eleven_flash_v2_5`'s half-credit rate,
    is a couple of credits out of a monthly ten thousand — and it validates the
    key, the voice id, the voice map and `ELEVENLABS_MODEL_ID` together, which is
    exactly the set that was wrong on the day this module was written.
    """
    from apps.media.adapters.tts import ElevenLabsTTSProvider

    provider = ElevenLabsTTSProvider()
    if not provider.is_configured():
        return Probe.skipped("elevenlabs", "no ELEVENLABS_API_KEY/ELEVENLABS_VOICE_ID set")

    # Every archetype's voice, not just the default: an unmapped key falls back to
    # `ELEVENLABS_VOICE_ID`, so a typo in one map entry is one companion that is
    # silent while every other companion speaks.
    # The archetype **key**, not the voice id it resolves to: `synthesize` resolves
    # its argument itself, so handing it an already-resolved id sends an unmapped
    # value through `resolve_voice` a second time and gets the default back. A
    # probe written that way tests the default voice once per map entry and never
    # touches the mapped ones -- passing green on exactly the typo it exists for.
    spoken: list[str] = []
    for key in ["", *settings.ELEVENLABS_VOICE_MAP]:
        voice = provider.resolve_voice(key)
        if not voice or voice in spoken:
            continue
        try:
            provider.synthesize("hello", key)
        except Exception as exc:
            return Probe(
                name="elevenlabs",
                ok=False,
                detail=f"voice '{voice}' ({key or 'ELEVENLABS_VOICE_ID'}): {exc}"[:400],
            )
        spoken.append(voice)

    return Probe(
        name="elevenlabs",
        ok=True,
        detail=f"{settings.ELEVENLABS_MODEL_ID} spoke with {', '.join(spoken)}",
    )


def probe_google_tts() -> Probe:
    """The fallback voice, which is only a fallback if it has a key.

    An empty `GOOGLE_TTS_API_KEY` is documented as meaning "no fallback yet", and
    it logs as `next=-` in the middle of an ElevenLabs outage. That is the right
    runtime behaviour and a thing to know *before* the outage.

    **The key goes in a header, never the query string.** Google accepts either,
    and `?key=` is what its own quickstarts show — but httpx logs every request
    URL at INFO, so that form writes the key into the worker log, into
    `launch_check --live` output, and into anything anyone pastes from either. It
    did exactly that on 15 August 2026 and cost a rotation
    (`Docs/Runbooks/Key-Rotation.md`).

    `adapters/tts.py` already knew this and says so where it builds the
    synthesis call. This probe was written later and did not inherit it; the two
    now agree, which is the only state in which the lesson is actually learned.
    """
    key = str(settings.GOOGLE_TTS_API_KEY or "")
    if not key:
        return Probe.skipped("google-tts", "no GOOGLE_TTS_API_KEY set")

    return _http_probe(
        "google-tts",
        "GET",
        "https://texttospeech.googleapis.com/v1/voices",
        headers={"X-Goog-Api-Key": key},
    )


def probe_vertex(model: str) -> Probe:
    """Generate a few tokens with `model`, on the service account we hold.

    **A real generation**, not a metadata read, for the reason `.env.example`
    documents at length: `client.models.get()` reports a model as present in a
    region where `generate_content` then 404s, so the cheap question has a
    different answer from the one the product asks. A handful of tokens is a
    rounding error against being wrong about this.

    It also catches the failure that has nothing to do with the model — a project
    whose billing lapsed answers `403 PERMISSION_DENIED / CONSUMER_INVALID` to
    every call, and no amount of correct configuration makes that visible offline.
    """
    from apps.common import vertex

    missing = vertex.missing_settings()
    if missing:
        return Probe.skipped(f"vertex:{model}", f"not configured: {', '.join(missing)} unset")

    try:
        vertex.call(
            lambda live: live.models.generate_content(
                model=model,
                contents="ping",
                config={"max_output_tokens": 8, "temperature": 0.0},
            )
        )
    except Exception as exc:
        # Bare `Exception`: the SDK raises `ClientError`, `ServerError`,
        # `google.auth` errors and plain `RuntimeError` for a closed pool, and a
        # preflight that only knew the first would crash on the others -- while
        # reporting nothing about the four providers after it in the loop.
        return Probe(name=f"vertex:{model}", ok=False, detail=f"{type(exc).__name__}: {exc}"[:400])

    return Probe(
        name=f"vertex:{model}",
        ok=True,
        detail=f"generated in {settings.GOOGLE_CLOUD_LOCATION} "
        f"on project {settings.GOOGLE_CLOUD_PROJECT}",
    )


def probe_gemini_image() -> Probe:
    """Draw one small picture through the adapter the product uses.

    **This one costs money** — a few cents — and it is the only probe here that
    does. It earns it by being the only way to answer the question `.env.example`
    warns about in its own paragraph: model availability on Vertex is per-region
    and per-allowlist, `GOOGLE_IMAGE_LOCATION` may differ from the text models'
    region, and every `imagen-*` id 404s on a plain project in a way that reads
    like a permissions problem. A metadata read answers a different question.

    Called through `GoogleImageProvider` rather than reimplemented, so the request
    this makes is the request a beat makes — the `response_modalities` and all.
    A probe with its own copy of the call is a probe that can pass while the
    product fails.
    """
    from apps.common import vertex
    from apps.media.adapters import image as image_adapters

    missing = vertex.missing_settings()
    if missing:
        return Probe.skipped("google-image", f"not configured: {', '.join(missing)} unset")

    provider = image_adapters.GoogleImageProvider()
    try:
        drawn = provider.generate("a single smooth grey pebble on a plain white background")
    except Exception as exc:
        return Probe(name="google-image", ok=False, detail=f"{type(exc).__name__}: {exc}"[:400])

    return Probe(
        name="google-image",
        ok=True,
        detail=f"{settings.GOOGLE_IMAGE_MODEL} returned {len(drawn)} bytes",
    )


def probe_embeddings() -> Probe:
    """The fourth child-data route, and the quietest one when it breaks.

    A failed beat is a fallback beat and a safety incident; a failed embedding is
    a memory stored with a null vector and a log line nobody reads. The product
    keeps working and stops remembering — `memory.retrieve ... stored=0` in the
    same logs where every other symptom was loud — and semantic recall is the part
    §11.3 calls the moat.

    Through `embed()` rather than the provider, because that seam is also where
    the width guard lives: an `EMBEDDING_DIMENSIONS` that is not the column's is a
    configuration error that no amount of working credentials fixes.
    """
    from apps.media.adapters import embedding as embedding_adapters
    from apps.media.exceptions import MediaConfigurationError

    try:
        provider = embedding_adapters.select_embedding_provider()
    except MediaConfigurationError as exc:
        return Probe.skipped("embeddings", str(exc)[:200])

    name = embedding_adapters.provider_label(provider)
    try:
        vector = embedding_adapters.embed("a dragon who is afraid of the dark", provider)
    except Exception as exc:
        return Probe(name="embeddings", ok=False, detail=f"{type(exc).__name__}: {exc}"[:400])

    return Probe(name="embeddings", ok=True, detail=f"{name} returned {len(vector)} dimensions")


def probe_all() -> list[Probe]:
    """Every vendor this deployment will call, in the order a story needs them.

    The *configured* models and providers, read the same way the product reads
    them, so a probe cannot pass on a route nothing uses — which is the failure
    mode of every preflight that hardcodes what it checks.
    """
    from apps.common import flags

    probes: list[Probe] = []

    if "google" in settings.AI_PROVIDER_ORDER:
        # The **effective** writer, not the setting: `ai.beat_model` is the
        # no-deploy rollback lever and the router reads it first, so a preflight
        # that probed `AI_BEAT_MODEL` could pass on a model nothing calls. Same
        # reasoning as `launch_check._effective_beat_model`, and deduplicated
        # because a deployment may legitimately have the flag set to the setting.
        writer = str(flags.value("ai.beat_model") or settings.AI_BEAT_MODEL)
        grader = str(settings.AI_SAFETY_MODEL or "")
        probes += [probe_vertex(model) for model in dict.fromkeys(m for m in (writer, grader) if m)]

    # Not gated on `AI_PROVIDER_ORDER`: `EMBEDDING_PROVIDER` is its own setting and
    # is deliberately switchable on its own (`local` needs no key at all), so the
    # embedding route is asked about itself rather than about the router's.
    if settings.EMBEDDING_PROVIDER not in ("", "local"):
        probes.append(probe_embeddings())

    # A fallback counts. It is the leg of the chain that is only ever exercised
    # during an outage, which makes it the one nobody notices is broken.
    images = [settings.IMAGE_PROVIDER, *settings.IMAGE_FALLBACK_PROVIDERS]
    voices = [settings.TTS_PROVIDER, *settings.TTS_FALLBACK_PROVIDERS]

    if "cloudflare" in images:
        probes.append(probe_cloudflare())
    if "google" in images:
        probes.append(probe_gemini_image())
    if "elevenlabs" in voices:
        probes.append(probe_elevenlabs())
    if "google" in voices:
        probes.append(probe_google_tts())

    return probes
