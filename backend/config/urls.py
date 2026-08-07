"""Root URL configuration.

Product endpoints are versioned in the path (`/api/v1/`, §18.2) so long-lived
family installs keep working across breaking changes. The health check and the
OpenAPI surface sit *outside* the version prefix on purpose — they describe the
service, not the product API, and must not move when v2 arrives.
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.common.views import HealthView

API_V1 = "api/v1/"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", HealthView.as_view(), name="health"),
    path(API_V1, include("apps.accounts.urls")),
    path(API_V1, include("apps.profiles.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
