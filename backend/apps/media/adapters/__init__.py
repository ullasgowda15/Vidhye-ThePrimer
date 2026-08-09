"""Provider-swappable media adapters (PRODUCT.md §16.6, §19.3).

Same shape as the model router: an ordered list of providers in configuration,
the first configured one wins, and the not-yet-built ones are typed stubs rather
than absences — so adding a vendor is filling in one method, not restructuring
the pipeline.

The voice chain goes one step further and fails over *at call time*
(`synthesize_with_failover`), which the image chain deliberately does not. A beat
with no picture is playable; a beat with no voice is a screen a pre-reader cannot
read (§4.9).
"""

from apps.media.adapters.image import ImageProvider, select_image_provider
from apps.media.adapters.tts import (
    TTSProvider,
    select_tts_provider,
    select_tts_providers,
    synthesize_with_failover,
)

__all__ = [
    "ImageProvider",
    "TTSProvider",
    "select_image_provider",
    "select_tts_provider",
    "select_tts_providers",
    "synthesize_with_failover",
]
