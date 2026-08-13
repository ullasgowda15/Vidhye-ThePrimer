"""The image moderation call behind the scan (PRODUCT.md §13.8, §13.2's G3).

Day 3 left `media.safety.scan` as a structural check with a `TODO` on it. This is
that TODO: a real classifier, behind a Protocol, that looks at the pixels and says
whether a three-year-old may see them.

**Why a model and not a library.** The things §15.9 forbids — realistic human
children, scary imagery, weapons, text painted into the frame — are semantic, and
the local NSFW classifiers that would run in-process detect roughly one of those
four. A vision model reads the picture the way the parent complaining about it
will.

**Why it is a separate model from the generator, again.** Same argument as
`apps.safety.classifier`: the pipeline that produced the image does not get to
approve it.

**Why `auto` rather than "always on".** The moderation call needs credentials, and
there is no honest way to have a credential-free installation both run a vision
model and tell the truth about it. So: when a provider is configured the gate is
real, and when it is not the structural checks stand alone and every scan logs
`moderation=absent`. That is visible in operations and provable in a test, which
"we intended to turn it on" is not. `prod.py` refuses to boot without it.

**Fail closed.** A moderation call that errors returns `FLAGGED`, not `PASSED`:
the caller withholds the asset and `orchestrator.illustrate_beat` substitutes safe
stock art, so the cost of the classifier being down is a stock picture rather than
an unscanned one. That is the opposite of `apps.safety.classifier`'s posture and
deliberately so — there, refusing would cost the child their story; here, it costs
one illustration on a beat that is still fully playable (§4.9).
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Protocol, cast, runtime_checkable

from django.conf import settings

from apps.media.models import SafetyStatus

logger = logging.getLogger(__name__)

#: The mode names `IMAGE_MODERATION` takes.
AUTO = "auto"
OFF = "off"

#: Only these are worth sending to a vision model. An SVG is ours (the stock
#: illustrations), audio has no pixels, and `application/octet-stream` never
#: reaches here because the structural check has already refused it.
MODERATABLE = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

#: What the moderator is asked. A rubric, not a persona — and phrased as a list of
#: refusals so a "no" needs a reason and a "yes" does not need an essay.
MODERATION_PROMPT = """\
You are a child-safety image classifier for a storybook read by children aged 3 \
to 7. Answer with a single word on the first line: SAFE or UNSAFE. If UNSAFE, put \
a short reason on the second line.

Answer UNSAFE if the image contains any of: violence, injury, blood, weapons; a \
frightening, threatening or menacing figure; anything sexual or adult; a \
photorealistic depiction of a real human child; nudity; drugs, alcohol or \
smoking; distressing or gory imagery; or readable text, a watermark or a logo \
painted into the picture.

Answer SAFE for an ordinary gentle children's-storybook illustration, including \
friendly animals, imaginary creatures with kind faces, weather, plants, \
buildings and everyday objects. A picture being plain, dull or badly drawn is \
SAFE. Do not invent problems.\
"""


class ModerationUnavailable(Exception):
    """No moderator could be run. The caller decides what that means."""


@runtime_checkable
class ImageModerator(Protocol):
    """One vision classifier, reduced to what the scan needs."""

    name: str

    def is_configured(self) -> bool: ...

    def classify(self, data: bytes, content_type: str) -> tuple[bool, str]:
        """Return `(is_safe, reason)`. Raises on a provider failure."""
        ...


class ClaudeImageModerator:
    """Claude's vision model as the image gate (§16.1)."""

    name = "anthropic-vision"

    #: Enough for one word and a short reason, and small enough that a model
    #: which decides to write an essay is truncated rather than expensive.
    MAX_TOKENS = 100

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or settings.ANTHROPIC_API_KEY
        self._model = model or settings.IMAGE_MODERATION_MODEL

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def classify(self, data: bytes, content_type: str) -> tuple[bool, str]:
        import anthropic

        # `cast` rather than a literal in the call: `media_type` is a runtime
        # string from `sniff_content_type` (guarded by `MODERATABLE`), and the
        # SDK's block union is typed on a `Literal` it cannot narrow a variable
        # to. The set of values that reach here is the constant above.
        content: list[Any] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": content_type,
                    "data": base64.b64encode(data).decode("ascii"),
                },
            },
            {"type": "text", "text": "Classify this illustration."},
        ]

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self._model,
            max_tokens=self.MAX_TOKENS,
            system=MODERATION_PROMPT,
            messages=[{"role": "user", "content": content}],
        )

        return self._verdict_from(response)

    @staticmethod
    def _verdict_from(response: Any) -> tuple[bool, str]:
        """Read `(is_safe, reason)` out of a response. Separated so it is testable.

        The parsing is where a moderator quietly stops moderating, so it is worth
        being able to drive it without a network: a model that starts prefixing
        its answers, or answering in a sentence, must not become a permanent pass.
        """
        # A refusal from the provider's own classifier is itself a verdict, and
        # the safe reading of it is "do not show this to a child".
        if getattr(response, "stop_reason", None) == "refusal":
            return False, "provider_refused"

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        if not text:
            raise ModerationUnavailable("The moderator returned no verdict.")

        head, _, tail = text.partition("\n")
        if head.strip().upper().startswith("SAFE"):
            return True, ""
        # Anything that is not an explicit SAFE is treated as unsafe, including a
        # model that answered in a shape we did not ask for. A classifier whose
        # unparseable answers mean "fine" is not a classifier.
        return False, (tail.strip() or head.strip() or "unsafe")[:120]


class GeminiImageModerator:
    """Gemini's vision model as the image gate, on Vertex AI (§16.1, §13.8).

    The same rubric, the same shape of answer and the same posture as
    `ClaudeImageModerator` — it exists because the deployment described in
    `Docs/Hosting.md §3` has no Anthropic key at all, and without it the gate
    silently degrades to the structural check. That is the failure the audit
    called out by name: the vision layer behind the F-1/F-2 companion-injection
    fix would not run, and every scan would log `moderation=absent` on a
    deployment that believed it was moderating.

    **Thinking is switched off here and nowhere else.** The answer is one word;
    `MAX_TOKENS` is 100 so that a model which decides to write an essay is
    truncated rather than expensive. A Gemini 2.5 model with dynamic thinking
    would happily spend all hundred of those tokens reasoning and return no text
    at all — which fails closed, so every generated illustration in the product
    would be flagged and replaced with stock art, with nothing but a
    `ModerationUnavailable` in a log to say why.
    """

    name = "google-vision"

    #: Same budget as the Claude moderator, and the same reason.
    MAX_TOKENS = 100

    def __init__(self, model: str = "") -> None:
        self._model = model or settings.IMAGE_MODERATION_MODEL

    def is_configured(self) -> bool:
        from apps.common import vertex

        return vertex.is_configured()

    def classify(self, data: bytes, content_type: str) -> tuple[bool, str]:
        from google.genai import types

        from apps.common import vertex

        response = vertex.client().models.generate_content(
            model=self._model,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=data, mime_type=content_type),
                        types.Part(text="Classify this illustration."),
                    ],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=MODERATION_PROMPT,
                max_output_tokens=self.MAX_TOKENS,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return self._verdict_from(response)

    @staticmethod
    def _verdict_from(response: Any) -> tuple[bool, str]:
        """Read `(is_safe, reason)` out of a response. Separated so it is testable.

        Deliberately parallel to `ClaudeImageModerator._verdict_from`, including
        the two things that are easy to get backwards:

        * **The provider's own refusal is a BLOCK verdict, not an error.** Gemini
          declining to describe a picture is itself information about the picture,
          and the safe reading of it is "do not show this to a child". Note that
          the safety settings are left at Gemini's defaults here — unlike the beat
          path (`router._PERMISSIVE_SAFETY_CATEGORIES`), where the vendor filter
          would be second-guessing our own grader. Here it is a second opinion on
          exactly the question being asked, and its answer is usable.
        * **An unparseable answer is not a pass.** A classifier whose confusing
          replies mean "fine" is not a classifier. Only an explicit SAFE passes;
          no text at all is `ModerationUnavailable`, which the caller fails closed.
        """
        feedback = getattr(response, "prompt_feedback", None)
        if getattr(feedback, "block_reason", None) is not None:
            return False, "provider_refused"

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise ModerationUnavailable("The moderator returned no verdict.")

        candidate = candidates[0]
        finish = str(getattr(getattr(candidate, "finish_reason", None), "value", "") or "")
        if finish in _GEMINI_REFUSALS:
            return False, "provider_refused"

        content = getattr(candidate, "content", None)
        text = "".join(
            str(part_text)
            for part in (getattr(content, "parts", None) or [])
            if not getattr(part, "thought", False) and (part_text := getattr(part, "text", ""))
        ).strip()
        if not text:
            raise ModerationUnavailable("The moderator returned no verdict.")

        head, _, tail = text.partition("\n")
        if head.strip().upper().startswith("SAFE"):
            return True, ""
        return False, (tail.strip() or head.strip() or "unsafe")[:120]


#: Gemini finish reasons that mean *the picture* was the problem. Read as a BLOCK
#: verdict rather than as a broken moderator: a vision model refusing to look at
#: an illustration has told us what we asked.
_GEMINI_REFUSALS = frozenset(
    {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}
)

#: `IMAGE_MODERATION_PROVIDER` -> the classifier it names. `auto` tries them in
#: this order and takes the first with credentials, which is what keeps the
#: default behaviour of a deployment that has only an Anthropic key exactly what
#: it was before Gemini existed here.
MODERATORS: dict[str, type] = {
    ClaudeImageModerator.name: ClaudeImageModerator,
    GeminiImageModerator.name: GeminiImageModerator,
    # The provider names as they are spelled everywhere else in the settings
    # (`AI_PROVIDER_ORDER`, `TTS_PROVIDER`), so an operator writing
    # `IMAGE_MODERATION_PROVIDER=google` gets what they meant.
    "anthropic": ClaudeImageModerator,
    "google": GeminiImageModerator,
}

#: The order `auto` tries them in.
_AUTO_ORDER = (ClaudeImageModerator, GeminiImageModerator)


def select_moderator() -> ImageModerator | None:
    """The configured moderator, or `None` when the gate is structural-only.

    `None` is a legitimate configuration (CI, offline development, `off`) and is
    reported by the caller as `moderation=absent`; it is never silently equivalent
    to a clean verdict.

    `IMAGE_MODERATION_PROVIDER` names one, and defaults to `auto`: try each in
    turn and use the first that has credentials. Two things about that default
    are load-bearing. It leaves an Anthropic-only deployment behaving exactly as
    it did — the gate is not a place to surprise anyone — and it means the
    all-Google deployment gets a *real* vision gate from its Vertex credentials
    alone, rather than the structural fallback it would otherwise have had while
    reporting `IMAGE_MODERATION=auto`.

    A name that is set but unknown, or set but not configured, returns `None`
    with a log line rather than falling back to a different provider: an operator
    who asked for Gemini and silently got Claude (or got nothing) has a
    deployment that does not match the one they described.
    """
    if str(settings.IMAGE_MODERATION).lower() == OFF:
        return None

    chosen = str(getattr(settings, "IMAGE_MODERATION_PROVIDER", "") or AUTO).strip().lower()
    if chosen and chosen != AUTO:
        factory = MODERATORS.get(chosen)
        if factory is None:
            logger.error(
                "media.moderation_provider_unknown provider=%s known=%s",
                chosen,
                ",".join(sorted(MODERATORS)),
            )
            return None
        moderator = factory()
        if moderator.is_configured():
            return cast("ImageModerator", moderator)
        logger.error(
            "media.moderation_provider_unconfigured provider=%s", moderator.name
        )
        return None

    for candidate in _AUTO_ORDER:
        moderator = candidate()
        if moderator.is_configured():
            return cast("ImageModerator", moderator)
    return None


def classify(data: bytes, content_type: str) -> tuple[str, str]:
    """Moderate one stored-shaped asset. Returns `(SafetyStatus, reason)`.

    `PENDING` means "this asset was not moderated" — a content type with no
    pixels, or no configured moderator — and is the caller's cue to log the gap
    rather than to record a pass it did not obtain.
    """
    if content_type not in MODERATABLE:
        return SafetyStatus.PENDING, "not_moderatable"

    moderator = select_moderator()
    if moderator is None:
        return SafetyStatus.PENDING, "no_moderator"

    try:
        is_safe, reason = moderator.classify(data, content_type)
    except Exception as exc:
        # Fail closed. See the module docstring: a stock illustration is a cheap
        # outcome and an unmoderated one is not.
        logger.exception("media.moderation_failed provider=%s", moderator.name)
        return SafetyStatus.FLAGGED, f"moderator_error:{type(exc).__name__}"

    if is_safe:
        return SafetyStatus.PASSED, ""
    return SafetyStatus.BLOCKED, f"moderator:{reason}"
