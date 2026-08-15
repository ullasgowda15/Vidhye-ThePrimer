"""Reading routes (Docs/Build/Reading-Integration.md §8). Mounted under `/api/v1/`.

No trailing slash, matching the child, companion and adventure routes, so a POST
is never redirected — which would drop its body, and this endpoint's body is an
audio file.
"""

from django.urls import path

from apps.reading.views import ReadingView

urlpatterns = [
    path(
        "children/<uuid:child_id>/reading",
        ReadingView.as_view(),
        name="child-reading",
    ),
]
