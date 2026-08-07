from django.contrib import admin

from apps.companion.models import Companion


@admin.register(Companion)
class CompanionAdmin(admin.ModelAdmin):
    list_display = ["name", "archetype", "child", "voice_id", "created_at"]
    list_filter = ["archetype"]
    search_fields = ["name", "archetype", "child__first_name"]
    readonly_fields = ["created_at", "updated_at"]
