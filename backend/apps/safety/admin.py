"""Prompt governance in the ops console (PRODUCT.md §19.2).

Visible and rollback-able, but not editable: a shipped prompt version is a
record of what a child was actually told. Changing one is a new row and a new
migration, reviewed like code.
"""

from django.contrib import admin

from apps.safety.models import PromptVersion


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    list_display = ["name", "version", "active", "created_by", "created_at"]
    list_filter = ["name", "active"]
    search_fields = ["name", "version", "template"]
    # `active` stays writable so a bad prompt can be rolled back without a deploy.
    readonly_fields = ["name", "version", "template", "model_config", "created_by"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
