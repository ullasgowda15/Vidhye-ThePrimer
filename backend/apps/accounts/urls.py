"""Auth + account routes (PRODUCT.md §18.3). Mounted under `/api/v1/`."""

from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("auth/signup", views.SignupView.as_view(), name="auth-signup"),
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("me", views.MeView.as_view(), name="me"),
    path("consent", views.ConsentView.as_view(), name="consent"),
    # Privacy centre (§12.8). `account/export` sits above `account` so the more
    # specific path is matched first — they are different verbs on different paths
    # and cannot actually collide, but the ordering is the one that reads correctly.
    path("privacy", views.PrivacyDisclosureView.as_view(), name="privacy"),
    path("account/export", views.AccountExportView.as_view(), name="account-export"),
    path("account", views.AccountDeleteView.as_view(), name="account-delete"),
]
