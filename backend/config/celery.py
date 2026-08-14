"""Celery application (PRODUCT.md §16.3).

Long/async work — memory consolidation, mastery updates, running summaries,
media prefetch — runs here rather than in the request path.

    celery -A config worker -l info
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("primer")

# All Celery config lives in Django settings under the CELERY_ prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
