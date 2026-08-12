"""Child profile + settings routes (PRODUCT.md §18.3, §12.7). Mounted under `/api/v1/`."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.profiles.views import ChildSettingsView, ChildViewSet

# SimpleRouter, not DefaultRouter: we don't want an API-root index page in the
# schema. No trailing slash, to match the paths written in §18.3 and keep POSTs
# from depending on an APPEND_SLASH redirect (which drops the body).
router = SimpleRouter(trailing_slash=False)
router.register("children", ChildViewSet, basename="child")

urlpatterns = [
    # Above the router's own `children/<pk>` detail route. The two cannot actually
    # collide — `settings` is a second segment, and the detail route's `pk`
    # converter is a UUID — but the settings route is the more specific one and
    # listing it first is how that stays true if the router's converter ever
    # loosens.
    path(
        "children/<uuid:child_id>/settings",
        ChildSettingsView.as_view(),
        name="child-settings",
    ),
    *router.urls,
]
