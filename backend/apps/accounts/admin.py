"""Django admin is our internal ops + moderation console (PRODUCT.md §7.11)."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import ConsentRecord, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["-created_at"]
    list_display = ["email", "display_name", "locale", "is_active", "is_staff", "created_at"]
    list_filter = ["is_active", "is_staff", "is_superuser", "locale"]
    search_fields = ["email", "display_name"]
    readonly_fields = ["created_at", "updated_at", "last_login"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Profile"), {"fields": ("display_name", "locale")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Dates"), {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "display_name", "password1", "password2"),
            },
        ),
    )


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    """Read-mostly by design: this table is legal evidence (§13.7).

    Records can be revoked from here (that is an ops action a parent may ask
    for), but nothing else about a grant can be edited after the fact — an
    editable audit trail is not an audit trail.
    """

    list_display = ["parent", "consent_type", "method", "granted_at", "revoked_at", "ip"]
    list_filter = ["consent_type", "method", "revoked_at"]
    search_fields = ["parent__email", "evidence_ref"]
    autocomplete_fields = ["parent"]
    date_hierarchy = "granted_at"
    readonly_fields = [
        "parent",
        "child",
        "consent_type",
        "method",
        "granted_at",
        "ip",
        "evidence_ref",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request) -> bool:
        # Consent is granted by a parent through the product, never typed in by us.
        return False
