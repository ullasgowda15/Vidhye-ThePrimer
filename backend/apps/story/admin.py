from django.contrib import admin

from apps.story.models import PlaySession


@admin.register(PlaySession)
class PlaySessionAdmin(admin.ModelAdmin):
    list_display = ["child", "started_at", "ended_at", "duration_s", "device", "ended_reason"]
    list_filter = ["ended_reason", "device"]
    search_fields = ["child__first_name"]
    date_hierarchy = "started_at"
    readonly_fields = ["created_at", "updated_at", "started_at"]
