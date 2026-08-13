"""Media pipeline failures.

Separated from the adapters so the service, the tasks, and the views can catch
them without importing a provider.

Nothing here is ever rendered to a child. The child-facing rule (§13) is that a
failure becomes a fallback — the previous illustration, a canonical expression,
or the story continuing without a new picture — never an error state.
"""


class MediaError(Exception):
    """Base class for media-pipeline failures."""


class MediaConfigurationError(MediaError):
    """No usable provider — missing key, unknown provider name."""


class MediaProviderError(MediaError):
    """A provider was reached but did not return a usable asset."""


class MediaBlocked(MediaError):
    """The safety scan refused the asset, so it was never stored (§13.8)."""


class MediaBudgetExceeded(MediaError):
    """A new asset was refused because the inference budget is spent (§18.8).

    A `MediaError` rather than a type of its own, and that is the breaker's whole
    design: the orchestrator has degraded gracefully on `MediaError` since Day 5 —
    the previous illustration stands, or the scene is narrated without one, and
    the beat stays playable — so a tripped breaker re-uses a tested path instead
    of adding an untested one on launch day.

    Note what it is *not*: this is only ever raised for an asset we do not already
    hold. Everything already in the media cache is served exactly as normal while
    the breaker is open, which is what "degrade to cached content" (§18.8) means
    here — not a mode the code enters, but what refusing new spend leaves behind.
    """


class MediaSwitchedOff(MediaError):
    """New illustrations are switched off by the `media.illustrations` flag.

    Distinct from `MediaBudgetExceeded` because the two are different facts about
    the world — somebody chose this one — and identical in consequence, which is
    why they share a base class and a handler.
    """
