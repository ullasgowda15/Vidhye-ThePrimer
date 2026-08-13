from django.contrib import admin

from apps.story.models import Adventure, Beat, Choice, PlaySession


@admin.register(PlaySession)
class PlaySessionAdmin(admin.ModelAdmin):
    list_display = ["child", "started_at", "ended_at", "duration_s", "device", "ended_reason"]
    list_filter = ["ended_reason", "device"]
    search_fields = ["child__first_name"]
    date_hierarchy = "started_at"
    readonly_fields = ["created_at", "updated_at", "started_at"]


class BeatInline(admin.TabularInline):
    """Beats read as a story, so they are shown in order inside their adventure.

    Read-only throughout: a beat is what a model generated and what a child was
    shown. Editing one in admin would silently rewrite the record of a session a
    parent can already see in the transparency log (§12.6).
    """

    model = Beat
    extra = 0
    can_delete = False
    # Unannotated, and written out twice rather than aliased. `django-stubs`
    # declares `fields` and `readonly_fields` as class variables of two *different*
    # union types on `BaseModelAdmin` — `fields` may hold a row-grouping tuple,
    # `readonly_fields` may not — so a `list[str]` annotation re-declares them as
    # instance variables and `readonly_fields = fields` hands the wider type to
    # the narrower field. The duplication is the honest version: everything shown
    # here is read-only, and a beat is what a model generated and a child was
    # shown, so the two lists must not be able to drift into disagreeing.
    fields = ["seq", "emotion", "narration", "safety_status", "media", "audio"]
    readonly_fields = ["seq", "emotion", "narration", "safety_status", "media", "audio"]
    ordering = ["seq"]

    def has_add_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(Adventure)
class AdventureAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "child",
        "status",
        "primary_objective_id",
        "difficulty",
        "started_at",
    ]
    list_filter = ["status", "primary_objective_id"]
    search_fields = ["title", "child__first_name"]
    date_hierarchy = "started_at"
    readonly_fields = ["created_at", "updated_at", "started_at", "plan", "emotion_arc"]
    inlines = [BeatInline]


@admin.register(Beat)
class BeatAdmin(admin.ModelAdmin):
    # `model` is listed and filterable because the question it answers is always
    # asked across rows: which model wrote the beats that got something wrong.
    list_display = ["adventure", "seq", "emotion", "model", "safety_status", "created_at"]
    list_filter = ["safety_status", "emotion", "model"]
    search_fields = ["narration", "adventure__title"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ["adventure", "beat", "chosen_key", "latency_ms", "retries", "assisted"]
    list_filter = ["assisted"]
    search_fields = ["chosen_key", "adventure__title"]
    readonly_fields = ["created_at", "updated_at"]
