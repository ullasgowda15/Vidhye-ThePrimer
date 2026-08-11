"""Companion routes (PRODUCT.md §18.3). Mounted under `/api/v1/`.

No trailing slash, matching the paths written in §18.3 and the child routes, so
a POST is never redirected (which would drop its body).
"""

from django.urls import path

from apps.companion.views import CompanionCatalogView, CompanionView, GreetingView

urlpatterns = [
    path(
        "children/<uuid:child_id>/companion",
        CompanionView.as_view(),
        name="companion-detail",
    ),
    path(
        "children/<uuid:child_id>/greeting",
        GreetingView.as_view(),
        name="companion-greeting",
    ),
    path("companion-catalog", CompanionCatalogView.as_view(), name="companion-catalog"),
]
