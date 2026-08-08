"""Adventure routes (PRODUCT.md §18.3). Mounted under `/api/v1/`.

No trailing slash, matching the paths written in §18.3 and the child and
companion routes, so a POST is never redirected (which would drop its body).
"""

from django.urls import path

from apps.story.views import AdventureCurrentView, AdventureStartView, adventure_stream

urlpatterns = [
    path(
        "children/<uuid:child_id>/adventures",
        AdventureStartView.as_view(),
        name="adventure-start",
    ),
    path(
        "children/<uuid:child_id>/adventures/current",
        AdventureCurrentView.as_view(),
        name="adventure-current",
    ),
    path(
        "adventures/<uuid:adventure_id>/stream",
        adventure_stream,
        name="adventure-stream",
    ),
]
