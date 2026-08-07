from django.contrib import admin

from apps.profiles.models import Child


@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = [
        "first_name",
        "parent",
        "birthdate",
        "locale",
        "pre_reader",
        "intensity_ceiling",
        "session_cap_min",
        "created_at",
    ]
    list_filter = ["locale", "pre_reader", "intensity_ceiling"]
    search_fields = ["first_name", "parent__email"]
    autocomplete_fields = ["parent"]
    readonly_fields = ["created_at", "updated_at"]
