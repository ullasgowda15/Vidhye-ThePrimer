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
]
