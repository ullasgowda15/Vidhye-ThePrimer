"""ASGI entrypoint.

The Primer is served under ASGI (uvicorn) rather than WSGI because story beats
stream to the client over SSE from async Django views (PRODUCT.md §16.4).

    uvicorn config.asgi:application
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

application = get_asgi_application()
