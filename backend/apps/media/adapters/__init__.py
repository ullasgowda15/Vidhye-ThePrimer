"""Provider-swappable media adapters (PRODUCT.md §16.6, §19.3).

Same shape as the model router: an ordered list of providers in configuration,
the first configured one wins, and the not-yet-built ones are typed stubs rather
than absences — so adding a vendor is filling in one method, not restructuring
the pipeline.
"""

from apps.media.adapters.image import ImageProvider, select_image_provider
from apps.media.adapters.tts import TTSProvider, select_tts_provider

__all__ = [
    "ImageProvider",
    "TTSProvider",
    "select_image_provider",
    "select_tts_provider",
]
