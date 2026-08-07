"""Shared Celery tasks."""

from celery import shared_task


@shared_task
def ping() -> str:
    """Prove a worker is connected to the broker and executing tasks.

    Usage (with `make worker` running in another shell):

        python manage.py shell -c \
            "from apps.common.tasks import ping; print(ping.delay().get(timeout=10))"
    """
    return "pong"
