"""Child profile routes (PRODUCT.md §18.3). Mounted under `/api/v1/`."""

from rest_framework.routers import SimpleRouter

from apps.profiles.views import ChildViewSet

# SimpleRouter, not DefaultRouter: we don't want an API-root index page in the
# schema. No trailing slash, to match the paths written in §18.3 and keep POSTs
# from depending on an APPEND_SLASH redirect (which drops the body).
router = SimpleRouter(trailing_slash=False)
router.register("children", ChildViewSet, basename="child")

urlpatterns = router.urls
