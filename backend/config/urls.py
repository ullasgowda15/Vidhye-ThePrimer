"""Root URL configuration.

Product endpoints are versioned in the path (`/api/v1/`, §18.2) so long-lived
family installs keep working across breaking changes. The health check and the
OpenAPI surface sit *outside* the version prefix on purpose — they describe the
service, not the product API, and must not move when v2 arrives.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.analytics import admax
from apps.analytics.internal import metrics_dashboard
from apps.common.views import HealthView

API_V1 = "api/v1/"

urlpatterns = [
    # §22's internal dashboard, above `admin/` so it resolves first. Deliberately
    # *not* under `api/v1/`: it is staff-only ops telemetry, and §5.6 keeps that
    # strictly apart from the parent-facing API. Living outside the versioned
    # prefix also keeps it out of the OpenAPI schema and therefore out of the
    # generated TS client, so the parent app cannot call it even by accident.
    path("admin/metrics", metrics_dashboard, name="internal-metrics"),
    path("admin/", admin.site.urls),
    # §22's dashboard again, behind its own two-factor sign-in (`apps.analytics.admax`).
    #
    # `/admin/metrics` above is the same numbers, and in production it is
    # effectively unreachable: `staff_member_required` bounces to the Django admin
    # login, whose stylesheets are never collected into the image. These four
    # routes depend on nothing from `django.contrib.admin` — their own login, their
    # own session keys, all CSS inline — so they work in an environment that serves
    # no static files at all.
    #
    # Outside `api/v1/` for the same reason `/admin/metrics` is: §5.6 keeps ops
    # telemetry strictly apart from the parent API, and living outside the versioned
    # prefix keeps it out of the OpenAPI schema and the generated TS client.
    path("admax", admax.dashboard, name="admax"),
    path("admax/login", admax.login, name="admax-login"),
    path("admax/verify", admax.verify, name="admax-verify"),
    path("admax/logout", admax.logout, name="admax-logout"),
    path("api/health/", HealthView.as_view(), name="health"),
    path(API_V1, include("apps.accounts.urls")),
    path(API_V1, include("apps.profiles.urls")),
    path(API_V1, include("apps.companion.urls")),
    path(API_V1, include("apps.story.urls")),
    path(API_V1, include("apps.reading.urls")),
    path(API_V1, include("apps.analytics.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]

# Admin CSS/JS in development. `django.contrib.staticfiles` only serves static
# files automatically under `runserver`, and we run under ASGI/uvicorn (§16) —
# so nothing answered `/static/admin/...` and the admin rendered unstyled. These
# patterns go through the staticfiles *finders*, so app-bundled assets are found
# without a `collectstatic` first. In production the CDN serves `STATIC_ROOT`
# and DEBUG is off, so this stays a development-only route.
if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()

# Generated illustrations and audio in development only. In staging and
# production these are objects in a bucket behind a CDN and Django never sees a
# request for them (§16.6) — serving blobs from the app tier is the thing the
# media pipeline exists to avoid.
if settings.DEBUG and settings.MEDIA_STORAGE != "s3":
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
