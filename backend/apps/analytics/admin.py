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

from apps.analytics.models import Event, GenerationCost, SessionReport


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


class AppendOnlyAdmin(admin.ModelAdmin):
    """A table nothing may add to, edit, or delete through the admin.

    Append-only is a property of the *writer* (`events.track`, `cost.record`) and
    the admin is the one place that property could be broken by hand — a metric
    recomputed after a row was edited is a metric nobody can trust, and the
    difference is invisible afterwards. Deletion is off too: `drain_events
    --prune` is how rows leave, because it will not remove a row that has not
    reached the export (§13.8), and the admin's delete button has no such
    scruples.
    """

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(Event)
class EventAdmin(AppendOnlyAdmin):
    """Raw telemetry (§17.4). Here to answer "is that service still emitting".

    Not a place to read one child. There is no `search_fields` on a child's name
    for that reason: the list filters are the *shape* of the pipeline — which
    events, when, for whom in the aggregate — and a staff page that could be
    pointed at one named four-year-old's afternoon is the thing §5.6 and §13.8
    keep apart from the parent's own dashboard.
    """

    # `child_id`, not `child`: rendering the FK would print the child's first
    # name in every row, which is precisely what the docstring above says this
    # page must not be. It also avoids a query per row.
    list_display = ["name", "ts", "child_id", "session_key", "exported_at"]
    list_filter = ["name", "ts", "exported_at"]
    date_hierarchy = "ts"
    readonly_fields = [field.name for field in Event._meta.fields]


@admin.register(GenerationCost)
class GenerationCostAdmin(AppendOnlyAdmin):
    """Per-generation spend (§19.9). The aggregate lives at `/admin/metrics`.

    This page is for the row-level question that an average hides: *did that
    generation get priced, and with what units?* `priced` is a filter for exactly
    that — a route missing from `apps.analytics.pricing` shows up here as a
    cluster of unpriced spans, which is the prompt to add it.
    """

    list_display = [
        "occurred_at",
        "kind",
        "operation",
        "provider",
        "model",
        "cost_usd",
        "priced",
        "latency_ms",
    ]
    list_filter = ["kind", "operation", "provider", "priced", "occurred_at"]
    date_hierarchy = "occurred_at"
    readonly_fields = [field.name for field in GenerationCost._meta.fields]
