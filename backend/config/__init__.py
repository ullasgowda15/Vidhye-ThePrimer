"""The Primer — Django project package.

Importing the Celery app here guarantees ``@shared_task`` decorators resolve to
this project's app regardless of import order (PRODUCT.md §16.3).
"""

from config.celery import app as celery_app

__all__ = ("celery_app",)
