"""WSGI entrypoint.

Kept for tooling that expects it. The real serving path is ASGI (see asgi.py) —
SSE streaming needs it.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

application = get_wsgi_application()
