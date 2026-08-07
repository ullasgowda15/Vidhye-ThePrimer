from django.contrib import admin

from apps.media.models import Media


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    list_display = ["id", "type", "provider", "safety_status", "child", "bytes", "created_at"]
    list_filter = ["type", "provider", "safety_status"]
    search_fields = ["id", "url", "style_hash", "character_hash", "scene_hash"]
    # Every column here is written by the pipeline. Editing one by hand would
    # either lie about what was scanned or silently break a cache key.
    readonly_fields = [
        "child",
        "type",
        "url",
        "provider",
        "prompt_hash",
        "style_hash",
        "character_hash",
        "scene_hash",
        "bytes",
        "created_at",
        "updated_at",
    ]
