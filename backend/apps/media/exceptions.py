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
