"""Admin for the parent's reports.

**Read-only in every field.** A session report is a record of what a parent was
told about one afternoon of their child's life (§12.1), and the whole value of it
is that it was written from what happened. A sentence edited here afterwards would
be indistinguishable from one the pipeline produced, which is precisely the
property §12.6 sells.

The page exists for one operational question, and `highlight_source` is the column
that answers it: *is the grounding gate working?* (§13.9). A run of `data` where
there used to be `model` means the check is rejecting everything a provider
returns; a week with no `quiet` at all means it is rejecting nothing.
"""

from typing import Any

from django.contrib import admin

from apps.analytics.models import SessionReport


@admin.register(SessionReport)
class SessionReportAdmin(admin.ModelAdmin):
    list_display = [
        "child",
        "occurred_at",
        "adventure_title",
        "ended_reason",
        "objective_code",
        "highlight_source",
    ]
    list_filter = ["highlight_source", "ended_reason", "objective_code"]
    search_fields = ["child__first_name", "adventure_title"]
    date_hierarchy = "occurred_at"
    readonly_fields = [field.name for field in SessionReport._meta.fields]

    def has_add_permission(self, request: Any) -> bool:
        # A report is written from a finished sitting and from nothing else.
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False
